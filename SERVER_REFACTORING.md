# Yuki Server Refactoring

## Overview

The original `server.py` file (542 lines) has been split into a modular structure for better maintainability, testability, and organization.

## New Structure

```
Yuki/
├── server/                     # Main server package
│   ├── __init__.py            # Package initialization
│   ├── app.py                 # Flask app setup and configuration
│   ├── config.py              # Configuration management
│   ├── tasks.py               # Celery background tasks
│   ├── utils.py               # Utility functions
│   └── routes/                # Route modules
│       ├── __init__.py
│       ├── upload.py          # File upload/download routes
│       ├── execution.py       # Job execution routes
│       ├── status.py          # Status and monitoring routes
│       ├── runner.py          # Runner management routes
│       └── workflow.py        # Workflow management routes
├── server_main.py             # Entry point and daemon management
├── server_compat.py           # Backward compatibility layer
└── server.py                  # Original file (can be kept or replaced)
```

## Key Benefits

### 1. **Separation of Concerns**
- **Configuration**: Centralized in `config.py`
- **Routes**: Logically grouped by functionality
- **Tasks**: Isolated Celery tasks in `tasks.py`
- **Utils**: Shared utilities in `utils.py`

### 2. **Improved Testability**
- Each module can be tested independently
- Easier to mock dependencies
- Better test isolation

### 3. **Better Maintainability**
- Smaller, focused files
- Clear module boundaries
- Easier to locate and modify specific functionality

### 4. **Scalability**
- Easy to add new routes without cluttering
- Clear structure for team development
- Modular imports reduce memory footprint

## Module Breakdown

### `app.py` (Flask Application)
- Flask app creation and configuration
- Blueprint registration
- Logging setup
- Main index route

### `config.py` (Configuration Management)
- Centralized configuration class
- Path management
- ConfigFile wrapper methods

### `tasks.py` (Celery Tasks)
- Background task definitions
- Celery app configuration
- Task execution logic

### `utils.py` (Utilities)
- Shared utility functions
- External service integration (REANA ping)

### Route Modules

#### `upload.py`
- File upload/download operations
- File export functionality
- File viewing routes

#### `execution.py`
- Job execution logic
- Task running
- Output management

#### `status.py`
- Status monitoring
- Job status updates
- Impression viewing

#### `runner.py`
- Runner registration/removal
- Runner connection testing
- Machine management

#### `workflow.py`
- Workflow operations
- Kill operations
- Result collection

## Migration Strategy

### Option 1: Gradual Migration
1. Keep original `server.py`
2. Import from new modules in `server.py`
3. Gradually replace functionality
4. Remove original when fully migrated

### Option 2: Direct Replacement
1. Replace `server.py` with `server_compat.py`
2. Update imports in other modules
3. Test thoroughly

### Option 3: Side-by-side
1. Keep both versions
2. Use feature flags to switch
3. Migrate clients gradually

## Testing Updates

The existing unit tests in `test_server.py` should continue to work with the compatibility layer. For new tests:

```python
# Test individual modules
from Yuki.server.routes.runner import bp as runner_bp
from Yuki.server.config import config

# Test with isolated components
```

## Configuration Changes

The new `YukiConfig` class provides a cleaner interface:

```python
from Yuki.server.config import config

# Old way
config_path = os.path.join(os.environ["HOME"], ".Yuki", "config.json")
config_file = ConfigFile(config_path)

# New way
config_file = config.get_config_file()
job_path = config.get_job_path(impression)
```

## Fixed Issues

The refactoring also addresses the bugs found in the original code:

1. ✅ **Missing return statement** in `register_machine` - Fixed in `routes/runner.py`
2. ✅ **KeyError in removerunner** - Fixed with safe deletion in `routes/runner.py`
3. ✅ **Function signature mismatch** in `runstatus` - Fixed in `routes/status.py`

## Backward Compatibility

The `server_compat.py` module ensures that existing code continues to work:

```python
# This still works
from Yuki.server import app, celeryapp, server_start
```

## Next Steps

1. **Test the new structure** with existing unit tests
2. **Update imports** in dependent modules
3. **Consider additional refactoring** (e.g., separating business logic from routes)
4. **Add more comprehensive logging** and error handling
5. **Consider adding API versioning** for future changes
