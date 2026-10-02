Remove-Item dhan_token.txt -ErrorAction SilentlyContinue
$env:DHAN_CLIENT_ID="1113602653"
$env:DHAN_PASSWORD="251984"
$env:DHAN_TOTP_SECRET="T4APPHMNWJ7D2QOY7E65CUCHNQYZQXAA"
$env:DRY_RUN="True"
$env:TEST_MODE="True"
python main.py