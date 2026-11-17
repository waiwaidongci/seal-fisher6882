import os
import sys

os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6379/15')
os.environ.setdefault('DATABASE_URL', 'sqlite://')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
