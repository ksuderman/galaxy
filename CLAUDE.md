# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Galaxy is an open, web-based platform for accessible, reproducible, and transparent computational research. This is a complex full-stack application with both Python backend and Vue.js frontend components.

## Quick Start

Start Galaxy development server:
```bash
sh run.sh
```

This will start Galaxy at http://localhost:8080

## Development Commands

### Environment Setup
```bash
# Setup development environment
make setup-venv
# or
bash scripts/common_startup.sh --dev-wheels
```

### Client (Frontend) Development
```bash
# Install client dependencies
make client-node-deps

# Build client for development (with watch/HMR)
make client-dev-server

# Build client for development
make client

# Build client for production
make client-production

# Run client tests
make client-test

# Client linting and formatting
make client-lint
make client-format
```

### Python Development
```bash
# Format Python code
make format

# Format only changed files since last commit
make diff-format

# Update dependencies
make update-dependencies
```

### Testing
```bash
# Run all Python unit tests
./run_tests.sh -unit

# Run specific unit tests
./run_tests.sh -unit test/unit/path/to/test.py

# Run API tests
./run_tests.sh -api

# Run integration tests
./run_tests.sh -integration

# Run Selenium tests
./run_tests.sh -selenium

# Run framework/tool tests
./run_tests.sh -framework

# Run CWL tests
./run_tests.sh -cwl

# Run specific test with pytest directly
pytest test/unit/specific_test.py -v
```

### Database Management
```bash
# Manage database schema
./manage_db.sh
```

## Code Architecture

### Backend (Python)
- **Main entry point**: `lib/galaxy/main.py` - Galaxy application entry point
- **Core app logic**: `lib/galaxy/app.py` - Main Galaxy application class
- **Web framework**: FastAPI-based API with some legacy components
- **Database**: SQLAlchemy ORM with Alembic migrations
- **Job execution**: Galaxy job system for tool execution
- **Configuration**: YAML-based config in `config/` directory

### Key Backend Directories
- `lib/galaxy/` - Core Galaxy Python code
- `lib/galaxy/webapps/galaxy/` - Galaxy web application
- `lib/galaxy/managers/` - Business logic managers
- `lib/galaxy/model/` - Database models
- `lib/galaxy/tools/` - Tool-related code
- `lib/galaxy/workflow/` - Workflow execution engine
- `lib/galaxy_test/` - Test suite

### Frontend (Vue.js)
- **Framework**: Vue 2.7 with TypeScript support
- **Build system**: Webpack with Yarn package management
- **State management**: Pinia stores
- **UI components**: Bootstrap Vue + custom components
- **Entry point**: `client/src/entry/analysis/` for main app

### Key Frontend Directories
- `client/src/components/` - Vue components
- `client/src/stores/` - Pinia state management
- `client/src/composables/` - Vue composition functions
- `client/src/utils/` - Utility functions
- `client/tests/` - Frontend test suite

### Important Configuration Files
- `pyproject.toml` - Python dependencies and tool configuration
- `client/package.json` - Frontend dependencies and scripts
- `config/galaxy.yml` - Main Galaxy configuration
- `Makefile` - Development automation commands

## Development Workflow

1. **Branch Strategy**: Use `dev` branch for new features, not `master`
2. **Code Style**: 
   - Python: Black formatter, ruff linter, isort imports
   - JavaScript/Vue: ESLint + Prettier
3. **Testing**: Write tests for new features, run relevant test suites
4. **Pull Requests**: Target the `dev` branch

## Common Tasks

### Adding a New API Endpoint
1. Add endpoint in appropriate manager in `lib/galaxy/managers/`
2. Add FastAPI route in `lib/galaxy/webapps/galaxy/`
3. Add tests in `lib/galaxy_test/api/`
4. Update OpenAPI schema: `make update-client-api-schema`

### Adding a New Vue Component
1. Create component in `client/src/components/`
2. Follow existing naming conventions (PascalCase)
3. Add TypeScript types where needed
4. Write unit tests in adjacent `.test.ts` files
5. Export from appropriate `index.ts` if creating a component library

### Working with Tools
- Tool definitions in `tools/` directory
- Tool execution handled by `lib/galaxy/tools/`
- Tool tests use framework in `test/functional/tools/`

### Database Changes
1. Create migration: `python scripts/manage_db.py revision -m "description"`
2. Update models in `lib/galaxy/model/`
3. Test migration: `python scripts/manage_db.py upgrade`

## Testing Strategy

- **Unit tests**: Fast, isolated tests for individual components
- **API tests**: Test Galaxy's REST API endpoints
- **Integration tests**: Test Galaxy configurations and workflows
- **Selenium tests**: Full browser testing of UI
- **Tool tests**: Test tool execution and framework features

Run relevant tests for your changes. The full test suite is large, so focus on the area you're modifying.

## Configuration Management

Galaxy uses YAML configuration files. Main config options:
- Development: Use default `config/galaxy.yml`
- Database: Configure `database_connection` 
- Tools: Tool configuration in `config/tool_conf.xml`
- Job execution: Configure runners in `config/job_conf.yml`

## Notes for Claude Code

- This is a large, mature codebase with both legacy and modern patterns
- Focus changes on the specific area being modified
- Follow existing code patterns and conventions
- Test thoroughly - Galaxy has complex tool execution and workflow systems
- Client build can be skipped during development with `GALAXY_SKIP_CLIENT_BUILD=1`
- Database migrations should be handled carefully
- Some areas still use legacy jQuery/Backbone alongside Vue.js
- Always run Python code with the `python3` executable.
- Logging statements should use `%` interpolation and not f-strings.
- 