"""
Simple in-memory caching for MCP Simpro Server.

Provides a thread-safe LRU cache for expensive operations like API calls.
Can be replaced with Redis in production if needed.
"""
from __future__ import annotations

import hashlib
import json
import time
from functools import wraps
from threading import Lock
from typing import Any, Callable, Optional

from .logger import get_logger

logger = get_logger(__name__)


class SimpleCache:
    """
    Thread-safe in-memory LRU cache with TTL support.
    
    Features:
    - LRU eviction when max_size is reached
    - Time-to-live (TTL) for entries
    - Thread-safe operations
    - Simple key-value storage
    
    Example:
        >>> cache = SimpleCache(max_size=100, default_ttl=300)
        >>> cache.set("key", "value")
        >>> cache.get("key")
        'value'
    """
    
    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        """
        Initialize cache.
        
        Args:
            max_size: Maximum number of entries (LRU eviction)
            default_ttl: Default time-to-live in seconds
        """
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: dict[str, tuple[Any, float]] = {}
        self._access_order: list[str] = []
        self._lock = Lock()
    
    def _is_expired(self, expires_at: float) -> bool:
        """Check if entry is expired"""
        return time.time() > expires_at
    
    def _evict_lru(self) -> None:
        """Evict least recently used entry"""
        if self._access_order:
            oldest_key = self._access_order.pop(0)
            self._cache.pop(oldest_key, None)
            logger.debug(f"Evicted LRU cache entry: {oldest_key}")
    
    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache.
        
        Args:
            key: Cache key
        
        Returns:
            Cached value or None if not found/expired
        """
        with self._lock:
            if key not in self._cache:
                return None
            
            value, expires_at = self._cache[key]
            
            # Check expiration
            if self._is_expired(expires_at):
                self._cache.pop(key)
                self._access_order.remove(key)
                logger.debug(f"Cache entry expired: {key}")
                return None
            
            # Update access order (move to end = most recently used)
            self._access_order.remove(key)
            self._access_order.append(key)
            
            logger.debug(f"Cache hit: {key}")
            return value
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if None)
        """
        with self._lock:
            # Evict if at capacity
            if len(self._cache) >= self.max_size:
                self._evict_lru()
            
            # Calculate expiration
            ttl = ttl or self.default_ttl
            expires_at = time.time() + ttl
            
            # Store value
            self._cache[key] = (value, expires_at)
            
            # Update access order
            if key in self._access_order:
                self._access_order.remove(key)
            self._access_order.append(key)
            
            logger.debug(f"Cache set: {key} (ttl={ttl}s)")
    
    def delete(self, key: str) -> None:
        """Delete entry from cache"""
        with self._lock:
            if key in self._cache:
                self._cache.pop(key)
                self._access_order.remove(key)
                logger.debug(f"Cache deleted: {key}")
    
    def clear(self) -> None:
        """Clear all cache entries"""
        with self._lock:
            self._cache.clear()
            self._access_order.clear()
            logger.info("Cache cleared")
    
    def size(self) -> int:
        """Get current cache size"""
        return len(self._cache)
    
    def stats(self) -> dict[str, Any]:
        """Get cache statistics"""
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "default_ttl": self.default_ttl
        }


# Global cache instance
cache = SimpleCache(max_size=1000, default_ttl=300)


def cached(ttl: Optional[int] = None, key_prefix: str = ""):
    """
    Decorator for caching function results.
    
    Args:
        ttl: Time-to-live in seconds (uses cache default if None)
        key_prefix: Prefix for cache key
    
    Example:
        >>> @cached(ttl=60, key_prefix="jobs")
        >>> def get_jobs(limit: int):
        >>>     return expensive_api_call(limit)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key from function name and arguments
            key_parts = [key_prefix, func.__name__]
            
            # Add args
            if args:
                key_parts.append(str(args))
            
            # Add kwargs (sorted for consistency)
            if kwargs:
                sorted_kwargs = sorted(kwargs.items())
                key_parts.append(str(sorted_kwargs))
            
            # Create hash of key parts
            key_str = "|".join(str(p) for p in key_parts)
            cache_key = hashlib.md5(key_str.encode()).hexdigest()
            
            # Try to get from cache
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                logger.debug(f"Cache hit for {func.__name__}")
                return cached_value
            
            # Call function and cache result
            logger.debug(f"Cache miss for {func.__name__}")
            result = func(*args, **kwargs)
            cache.set(cache_key, result, ttl=ttl)
            
            return result
        
        return wrapper
    return decorator