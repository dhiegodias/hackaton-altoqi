FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt && useradd --uid 1000 --create-home radar && chown radar:radar /app
COPY --chown=radar:radar radar radar
COPY --chown=radar:radar static static
COPY --chown=radar:radar tests tests
COPY pyproject.toml .
USER radar
EXPOSE 8000
ENTRYPOINT ["python", "-m", "radar.runtime"]
CMD ["uvicorn", "radar.api:app", "--host", "0.0.0.0", "--port", "8000"]
