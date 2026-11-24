#!/usr/bin/env bash
#
# Makes the certificate the Ad Studio signs in to Microsoft with, to save sets to
# SharePoint (D129). The private key is made here and never leaves the server:
# only the public certificate is uploaded to the "Dynamic Ad Studio" app
# registration (Certificates & secrets > Certificates > Upload).
#
#   sudo ./scripts/make_studio_cert.sh
#     /etc/da-studio/studio-write.pem       key + certificate, dauction only (0600)
#     /etc/da-studio/dynamic-ad-studio.cer  public certificate: upload this one
#
# Valid two years. To renew, delete studio-write.pem, run this again, upload the
# new .cer and remove the old certificate from the app registration.
set -euo pipefail
dir=/etc/da-studio
install -d -m 0750 -o root -g dauction "$dir"
if [ -e "$dir/studio-write.pem" ]; then
  echo "Already there: $dir/studio-write.pem (delete it first to make a new one)" >&2
  exit 1
fi
umask 077
openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 730 -subj "/CN=Dynamic Ad Studio" \
  -keyout "$dir/key.tmp" -out "$dir/cert.tmp" 2>/dev/null
cat "$dir/key.tmp" "$dir/cert.tmp" > "$dir/studio-write.pem"
openssl x509 -in "$dir/cert.tmp" -outform DER -out "$dir/dynamic-ad-studio.cer"
rm -f "$dir/key.tmp" "$dir/cert.tmp"
chown dauction:dauction "$dir/studio-write.pem"
chmod 0600 "$dir/studio-write.pem"
chmod 0644 "$dir/dynamic-ad-studio.cer"
echo "Made $dir/studio-write.pem and $dir/dynamic-ad-studio.cer"
echo "Thumbprint: $(openssl x509 -in "$dir/studio-write.pem" -noout -fingerprint -sha1 | cut -d= -f2 | tr -d :)"
echo "Expires:    $(openssl x509 -in "$dir/studio-write.pem" -noout -enddate | cut -d= -f2)"
