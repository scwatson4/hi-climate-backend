FROM python:3.11-slim-bullseye AS python-base

ENV PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=100 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_HOME="/opt/poetry" \
    POETRY_NO_INTERACTION=true \
    APP_PATH="/app" \
    VENV_PATH="/venv"

# prepend poetry and venv to path
ENV PATH="$POETRY_HOME/bin:$VENV_PATH/bin:$PATH"

WORKDIR $APP_PATH

######################### BUILDER #######################################
# Provides virtual environment with runtime dependencies to build the project
# Uses python venv to create the virtual environment and poetry to manage project dependencies

FROM python-base AS builder
RUN apt-get update && apt-get install --no-install-recommends -y \
        curl \
        build-essential \
        ffmpeg

# Install latest Poetry version, respects $POETRY_HOME
RUN curl -sSL https://install.python-poetry.org | python3 -

# Copy dependencies
COPY ./pyproject.toml ./
COPY ./poetry.lock* ./

# Create virtual environment
RUN python -m venv $VENV_PATH

# Install runtime dependencies (--no-dev), but exclude the project itself (--no-root), which avoids editable mode
RUN . $VENV_PATH/bin/activate && poetry install --only main --no-root

######################### DEVELOPMENT #######################################
# Provides a full development environment, with the project installed in editable mode
# Consider mounting local volume under /app (for example using docker-compose)

FROM python-base AS development
ENV ENV=development

# Copy poetry and venv
COPY --from=builder $POETRY_HOME $POETRY_HOME
COPY --from=builder $VENV_PATH $VENV_PATH

# Copy entrypoint & give execution permission
COPY entrypoint.sh /
RUN chmod +x /entrypoint.sh

# Copy full project and install it with development dependencies
COPY . .
RUN . $VENV_PATH/bin/activate && poetry install

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]

CMD ["poetry", "run", "uvicorn", "app.main:app", "--reload", "--port", "8000", "--host", "0.0.0.0"]