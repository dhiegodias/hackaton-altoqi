// Busqi extension to cloudflared: local session duration after verified email login.
package quicktunnelauth

import (
	"fmt"
	"os"
	"time"
)

func configuredBusqiSessionDuration() (time.Duration, error) {
	value := os.Getenv("BUSQI_TUNNEL_SESSION_DURATION")
	if value == "" {
		return 0, nil // Preserve upstream behavior outside the Busqi Compose.
	}
	duration, err := time.ParseDuration(value)
	if err != nil || duration < time.Hour || duration > 72*time.Hour {
		return 0, fmt.Errorf("BUSQI_TUNNEL_SESSION_DURATION must be between 1h and 72h")
	}
	return duration, nil
}
