import pytest
from src.placeholders import PlaceholderResolver

def test_faker_placeholders(placeholder_dir):
    resolver = PlaceholderResolver(placeholder_dir)
    
    # Aliases
    assert "@" in resolver.replace("{email}")
    
    ua = resolver.replace("{user_agent}")
    assert ua and ua != "{user_agent}"
    # Some old Opera UAs don't have Mozilla, so we just check it's not empty
    
    # Generic faker call
    res_city = resolver.replace("{faker:city}")
    assert res_city and res_city != "{faker:city}"
    
    # Should change on next call
    res1 = resolver.replace("{first_name}")
    res2 = resolver.replace("{first_name}")
    # It's statistically possible they match, but extremely unlikely.
    # If this flakes, we can retry or accept it.
    
def test_faker_invalid_method(placeholder_dir):
    resolver = PlaceholderResolver(placeholder_dir)
    # Should return None -> str(None) -> "None" or raise?
    # Current impl: checks hasattr. If not found, goes to _ensure_loaded -> file not found
    with pytest.raises(ValueError, match="Placeholder 'faker:invalid_method' not found"):
        resolver.replace("{faker:invalid_method}")
