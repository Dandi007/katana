#!/usr/bin/env python3
"""
Reddit API - No credentials needed for public read-only access.
Just need a proper User-Agent header.
"""

USER_AGENT = "Zettelkasten-Reddit-Skill/1.0 (by uther)"


def get_user_agent() -> str:
    """Get User-Agent for Reddit API requests"""
    return USER_AGENT
