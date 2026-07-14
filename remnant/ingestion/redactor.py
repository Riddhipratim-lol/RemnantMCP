import re
from typing import List, Optional

DEFAULT_REDACTION_RULES = [
    # GitHub Tokens
    r"ghp_[a-zA-Z0-9]{36}",
    # Generic API Keys / Secrets / Passwords in assignments, JSON, etc.
    r"(?i)(?:api[_-]?key|secret|private[_-]?key|auth[_-]?token|password|passwd|passphrase)(?:[\s'\"]*[:=][\s'\"]*|[\s'\"]+)([a-zA-Z0-9_\-\.\~]{16,})",
    # Database Connection Strings (specifically with passwords inside)
    r"[a-zA-Z0-9\+]+://[^:\s@]+:[^@\s]+@[^@\s]+/?[^?\s]*",
    # AWS Access Key ID & Secret Access Key
    r"AKIA[0-9A-Z]{16}",
    r"(?i)aws_secret_access_key[\s'\"]*[:=][\s'\"]*([a-zA-Z0-9/\+=]{40})"
]

def redact_content(content: str, custom_rules: Optional[List[str]] = None) -> str:
    """
    Scrub sensitive secrets from content using regex patterns.
    If a regex contains capture groups, only the first capture group is redacted.
    Otherwise, the entire matching string is redacted.
    """
    if not content:
        return ""
    
    rules = custom_rules if custom_rules is not None else DEFAULT_REDACTION_RULES
    redacted = content
    
    for rule in rules:
        compiled = re.compile(rule)
        
        def sub_fn(match):
            if match.groups():
                # We want to replace the first capture group
                start, end = match.span(1)
                match_str = match.group(0)
                match_start = start - match.start(0)
                match_end = end - match.start(0)
                return match_str[:match_start] + "<REDACTED>" + match_str[match_end:]
            else:
                return "<REDACTED>"
                
        redacted = compiled.sub(sub_fn, redacted)
        
    return redacted
