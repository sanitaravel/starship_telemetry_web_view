"""Pytest configuration and Hypothesis profiles for Starship Telemetry Backend."""

from hypothesis import settings, HealthCheck

# Default profile: balanced speed and thoroughness for CI/local dev
settings.register_profile(
    "default",
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow],
)

# CI profile: more thorough testing for continuous integration
settings.register_profile(
    "ci",
    max_examples=500,
    suppress_health_check=[HealthCheck.too_slow],
)

# Dev profile: fast iteration during development
settings.register_profile(
    "dev",
    max_examples=20,
    suppress_health_check=[HealthCheck.too_slow],
)

# Load the default profile
settings.load_profile("default")
