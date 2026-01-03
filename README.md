# Production-Grade Execution Agent

This project implements a FastAPI-based microservice designed to function as an Execution Agent within an automated trading system. It receives trade requests, places them with a broker via a pluggable adapter, and persists all state changes through a Database Agent. The system is designed to be safe, idempotent, observable, and testable.

## Core Features

- **FastAPI Application**: A robust and asynchronous API built with FastAPI.
- **Idempotent Order Creation**: Ensures that duplicate trade requests do not result in duplicate orders.
- **Pluggable Broker Adapters**: A clean interface that allows for easy integration with different brokers. Includes a `SimulatorAdapter` for paper trading and testing.
- **Asynchronous Execution**: Orders are processed in the background, ensuring the API remains responsive.
- **Database Agent Integration**: All order states are persisted via an external Database Agent, which is the single source of truth.
- **Structured Logging**: All log output is in JSON format, ready for integration with modern observability platforms.
- **API Key Security**: A simple but effective API key middleware protects all endpoints.

## Project Structure

```
execution_agent/
├── app/
│   ├── main.py             # FastAPI application, endpoints, middleware
│   ├── models.py           # Pydantic data models
│   ├── adapters/
│   │   ├── base.py         # Abstract BrokerAdapter interface
│   │   └── simulator.py    # Deterministic broker simulator
│   ├── services/
│   │   └── execution_service.py # Core business logic
│   ├── db_client.py        # Client for the Database Agent API
│   ├── logging.py          # Structured logging configuration
│   └── config.py           # Environment variable management
├── tests/
│   ├── test_orders.py      # Integration tests for API endpoints
│   ├── test_idempotency.py # Tests for idempotent order creation
│   └── test_adapter.py     # Unit tests for the SimulatorAdapter
├── requirements.txt        # Python package dependencies
└── README.md               # This file
```

## Setup and Installation

### 1. Install Dependencies

It is recommended to use a virtual environment.

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables

The application uses an `.env` file to manage environment variables. Create a file named `.env` in the project root.

```
# .env
API_KEY="your-secret-api-key"
```

If this file is not present, the application will use a default `API_KEY` of `"default_api_key"`.

## Running the Application

You can run the application using `uvicorn`.

```bash
uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`.

## Running Tests

The test suite validates the application's functionality, including API endpoints, idempotency, and the behavior of the `SimulatorAdapter`.

To run the tests, execute the following command from the project root:

```bash
python -m pytest
```
