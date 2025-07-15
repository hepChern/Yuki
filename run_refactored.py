#!/usr/bin/env python3
"""
Entry point for the refactored Yuki server.
Run this to start the new modular version.
"""

import sys
import os

# Add the parent directory to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Yuki.server import app, celeryapp
from Yuki.server_main import server_start, start_flask_app, start_celery_worker

def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Start Yuki Server')
    parser.add_argument('--mode', choices=['flask', 'celery', 'both'], 
                       default='both', help='Which component to start')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=3315, help='Port to bind to')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    
    args = parser.parse_args()
    
    if args.mode == 'flask':
        print("Starting Flask app only...")
        app.run(host=args.host, port=args.port, debug=args.debug)
    elif args.mode == 'celery':
        print("Starting Celery worker only...")
        start_celery_worker()
    else:
        print("Starting both Flask and Celery...")
        server_start()

if __name__ == '__main__':
    main()
