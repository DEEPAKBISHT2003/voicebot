# AI Mock Interviewer - Monorepo

AI-powered real-time voice screening platform for technical interviews.

## Features

- **Real-time Voice Interview**: AI-powered voice interviews with Deepgram transcription
- **Copilot Assistance**: AI copilot provides real-time suggestions to interviewers
- **Intelligence Reports**: Generate detailed reports with AI analysis
- **Mock Interview Mode**: Simulate interview sessions for practice
- **Teams Integration**: Join Teams meetings for live interviews

## Architecture

This is a **microservices monorepo** with two independent services:

### Interview Service (Port 8000)
- AI Mock Interviewer backend
- Voice pipeline coordination
- Interview session management

### Copilot Service (Port 8001)
- AI Copilot backend
- Intelligence reports
- Transcript analysis

## Quick Start

```bash
# Install dependencies
cd applications/frontend && npm install

# Start services with Docker
docker-compose up --build

# Run tests
bash scripts/test.sh
```

## Structure

```
demo/
├── packages/          # Shared packages
│   ├── domain-models/
│   ├── adapters/
│   ├── infrastructure/
│   └── types/
├── services/          # Backend services
│   ├── interview/     # Interview API
│   └── copilot/       # Copilot API
├── applications/      # Frontend applications
│   └── frontend/
├── tests/             # Test suites
│   └── integration/
├── infrastructure/    # Infrastructure as Code
├── scripts/           # Build and deployment scripts
├── docs/              # Documentation
├── .kiro/             # AI-DLC configuration
└── migration-plan-phase*.md
```

## Services

| Service | Port | Database | Description |
|---------|------|----------|-------------|
| Interview | 8000 | PostgreSQL:5432 | AI Interviewer API |
| Copilot | 8001 | PostgreSQL:5433 | AI Copilot API |
| Frontend | 3000 | - | React Application |

## Development

### Prerequisites
- Node.js 20+
- Python 3.12+
- Docker & Docker Compose
- bun (for AI-DLC)

### Setup
1. Clone the repository
2. Install dependencies: `cd applications/frontend && npm install`
3. Configure environment variables (see `.env.example`)
4. Start services: `docker-compose up --build`

### Running Tests
```bash
# Run all tests
bash scripts/test.sh

# Run with coverage
pytest services/interview/tests/ -v --cov=.
```

### Building
```bash
# Build all services
bash scripts/build.sh

# Build specific service
cd services/interview
docker build -t interview-service:latest .
```

## API Documentation

- [Interview API](docs/api/interview-endpoints.md)
- [Copilot API](docs/api/copilot-endpoints.md)
- [Architecture](docs/architecture/monorepo-structure.md)
- [Getting Started](docs/developer/getting-started.md)

## Contributing

1. Create a feature branch
2. Add tests for new functionality
3. Run lint and tests
4. Submit a pull request

## License

MIT