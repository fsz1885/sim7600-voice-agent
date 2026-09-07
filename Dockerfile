FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY src ./src
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
COPY tests ./tests
COPY examples ./examples
RUN useradd --create-home agent
USER agent
ENTRYPOINT ["voice-agent"]
CMD ["chat", "examples/company-registration.json"]
