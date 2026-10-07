package quicktunnelauth

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/cloudflare/cloudflared/connection"
	"github.com/go-jose/go-jose/v4"
	"github.com/go-jose/go-jose/v4/jwt"
	"github.com/stretchr/testify/require"
)

func TestBusqiSessionConfiguration(t *testing.T) {
	for _, value := range []string{"0", "-1h", "5s", "73h", "invalid"} {
		t.Run(value, func(t *testing.T) {
			t.Setenv("BUSQI_TUNNEL_SESSION_DURATION", value)
			manager, err := NewQuickTunnelAuthSessionManager()
			require.Error(t, err)
			require.Nil(t, manager)
		})
	}
	for _, value := range []string{"", "1h", "24h", "72h"} {
		t.Run("valid_"+value, func(t *testing.T) {
			t.Setenv("BUSQI_TUNNEL_SESSION_DURATION", value)
			manager, err := NewQuickTunnelAuthSessionManager()
			require.NoError(t, err)
			require.NotNil(t, manager)
		})
	}
}

func TestBusqiVerifiedLoginSurvivesBrokerExpiryAndStopsAfter24Hours(t *testing.T) {
	t.Setenv("BUSQI_TUNNEL_SESSION_DURATION", "24h")
	h := newTestQuickTunnelAuthHandlerHarness(t, []string{"allowed@example.com"})
	manager, err := NewQuickTunnelAuthSessionManager()
	require.NoError(t, err)
	now := h.now
	manager.now = func() time.Time { return now }
	h.handler.sessionManager = manager

	request := busqiSignedCallback(t, h, "allowed@example.com", h.now.Add(6*time.Second))
	response := httptest.NewRecorder()
	_, outcome, err := h.handler.AuthorizeHTTP(response, request)
	require.NoError(t, err)
	require.Equal(t, quickTunnelAuthOutcomeCallbackAuthorized, outcome)
	require.Equal(t, http.StatusSeeOther, response.Code)
	var cookie *http.Cookie
	for _, candidate := range response.Result().Cookies() {
		if candidate.Name == quickTunnelAuthSessionCookieName {
			cookie = candidate
		}
	}
	require.NotNil(t, cookie)
	require.Equal(t, h.now.Add(24*time.Hour), cookie.Expires)
	require.Equal(t, 86400, cookie.MaxAge)
	require.True(t, cookie.Secure && cookie.HttpOnly)

	for _, elapsed := range []time.Duration{time.Minute, 8 * time.Hour, 24*time.Hour - time.Second} {
		now = h.now.Add(elapsed)
		apiRequest := httptest.NewRequest(http.MethodGet, "https://example.trycloudflare.com/api/stats", nil)
		apiRequest.AddCookie(cookie)
		decision, _, err := h.handler.AuthorizeHTTP(httptest.NewRecorder(), apiRequest)
		require.NoError(t, err)
		require.Equal(t, connection.HTTPRequestAuthorizationAllowed, decision)
		require.Empty(t, apiRequest.Cookies(), "origin must not receive the access cookie")
	}

	now = h.now.Add(time.Hour)
	tampered := *cookie
	tampered.Value = "x" + tampered.Value[1:]
	valid, err := manager.ValidateSession(requestWithQuickTunnelAuthSession(tampered.Value))
	require.NoError(t, err)
	require.False(t, valid)

	restarted, err := NewQuickTunnelAuthSessionManager()
	require.NoError(t, err)
	restarted.now = manager.now
	valid, err = restarted.ValidateSession(requestWithQuickTunnelAuthSession(cookie.Value))
	require.NoError(t, err)
	require.False(t, valid, "restarting the tunnel must revoke existing sessions")

	now = h.now.Add(24 * time.Hour)
	apiRequest := httptest.NewRequest(http.MethodPost, "https://example.trycloudflare.com/api/leads", nil)
	apiRequest.AddCookie(cookie)
	response = httptest.NewRecorder()
	decision, _, err := h.handler.AuthorizeHTTP(response, apiRequest)
	require.NoError(t, err)
	require.Equal(t, connection.HTTPRequestAuthorizationHandled, decision)
	require.Equal(t, http.StatusUnauthorized, response.Code)
}

func TestBusqiLongSessionStillRejectsUnapprovedEmailAndExpiredIdentity(t *testing.T) {
	t.Setenv("BUSQI_TUNNEL_SESSION_DURATION", "24h")
	for _, test := range []struct {
		name    string
		email   string
		expires time.Duration
	}{
		{"unapproved_email", "other@example.com", time.Minute},
		{"expired_identity", "allowed@example.com", -time.Second},
	} {
		t.Run(test.name, func(t *testing.T) {
			h := newTestQuickTunnelAuthHandlerHarness(t, []string{"allowed@example.com"})
			manager, err := NewQuickTunnelAuthSessionManager()
			require.NoError(t, err)
			manager.now = func() time.Time { return h.now }
			h.handler.sessionManager = manager
			request := busqiSignedCallback(t, h, test.email, h.now.Add(test.expires))
			response := httptest.NewRecorder()
			decision, _, err := h.handler.AuthorizeHTTP(response, request)
			require.Error(t, err)
			if test.name == "unapproved_email" {
				require.ErrorIs(t, err, errQuickTunnelAuthRecipientNotAllowed)
			}
			require.Equal(t, connection.HTTPRequestAuthorizationHandled, decision)
			require.Equal(t, http.StatusForbidden, response.Code)
			for _, cookie := range response.Result().Cookies() {
				require.NotEqual(t, quickTunnelAuthSessionCookieName, cookie.Name)
			}
		})
	}
}

func busqiSignedCallback(t *testing.T, h *testQuickTunnelAuthHandlerHarness, email string, expires time.Time) *http.Request {
	t.Helper()
	login := beginTestQuickTunnelLogin(t, h.stateManager, "/")
	claims := newTestQuickTunnelAuthBrokerClaims(h.now, h.stateManager.hostname, login.State)
	claims.Email = email
	expiry := expires.Unix()
	claims.IdentityExpiresAt = &expiry
	if expires.After(h.now) && expires.Before(claims.Expiry.Time()) {
		claims.Expiry = jwt.NewNumericDate(expires)
	}
	assertion := signTestQuickTunnelAuthBrokerAssertion(t, jose.ES256, h.privateKey,
		h.verificationKey.KeyID, quickTunnelAuthBrokerJWTHeaderType, false, claims)
	return newTestQuickTunnelAuthCallbackRequest(login.Cookie, login.State, assertion)
}
