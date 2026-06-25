import re

with open('database.py', 'r', encoding='utf-8') as f:
    content = f.read()

# DATETIME(col, '+5 hours', '+30 minutes') -> CAST(col + INTERVAL '5 hours 30 minutes' AS TIMESTAMP)
content = re.sub(
    r"DATETIME\(([^,]+),\s*'\+5 hours',\s*'\+30 minutes'\)",
    r"CAST(\1 + INTERVAL '5 hours 30 minutes' AS TIMESTAMP)",
    content,
    flags=re.IGNORECASE
)

# DATE(col, '+5 hours', '+30 minutes') -> CAST(col + INTERVAL '5 hours 30 minutes' AS DATE)
content = re.sub(
    r"DATE\(([^,]+),\s*'\+5 hours',\s*'\+30 minutes'\)",
    r"CAST(\1 + INTERVAL '5 hours 30 minutes' AS DATE)",
    content,
    flags=re.IGNORECASE
)

# DATE('now', '+5 hours', '+30 minutes', ...) -> CAST(CURRENT_TIMESTAMP + INTERVAL '5 hours 30 minutes' + CAST(... AS INTERVAL) AS DATE)
content = re.sub(
    r"DATE\('now',\s*'\+5 hours',\s*'\+30 minutes',\s*([^)]+)\)",
    r"CAST(CURRENT_TIMESTAMP + INTERVAL '5 hours 30 minutes' + CAST(\1 AS INTERVAL) AS DATE)",
    content,
    flags=re.IGNORECASE
)

with open('database.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done')
