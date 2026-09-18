import os

filepath = "server/sms.py"
with open(filepath, "r") as f:
    content = f.read()

# Replace the api_key logic
old_api_key_logic = 'api_key = os.getenv("SMS_API_KEY") or os.getenv("AFRICASTALKING_API_KEY")'
new_api_key_logic = 'api_key = os.getenv("SMS_API_KEY") or os.getenv("AFRICASTALKING_API_KEY") or os.getenv("TEXT_SMS_API_KEY") or os.getenv("TEXTSMS_API_KEY")'

old_username_logic = 'username = os.getenv("SMS_USERNAME") or os.getenv("AFRICASTALKING_USERNAME", "sandbox")'
new_username_logic = 'username = os.getenv("SMS_USERNAME") or os.getenv("AFRICASTALKING_USERNAME") or os.getenv("TEXT_SMS_USERNAME") or os.getenv("TEXTSMS_USERNAME") or "sandbox"'

content = content.replace(old_api_key_logic, new_api_key_logic)
content = content.replace(old_username_logic, new_username_logic)

with open(filepath, "w") as f:
    f.write(content)
