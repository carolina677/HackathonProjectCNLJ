#!/usr/bin/env bash
# Put a running Watchtower (started with WATCHTOWER_LAB=1) into the state the demo video starts from:
# no portals, every lab portal restored, then the three real demo portals and two lab portals attached.
#   scripts/demo_reset.sh [http://localhost:8080] [--extended]
set -euo pipefail
API="${1:-http://localhost:8080}"
EXT="${2:-}"

echo "Deleting all portals on $API"
for id in $(curl -fsS "$API/portals" | python3 -c 'import json,sys; print(" ".join(p["id"] for p in json.load(sys.stdin)))'); do
  curl -fsS -X DELETE "$API/portals/$id" >/dev/null
done

echo "Restoring lab portals"
for slug in ssr spa multistep summary-redirect login captcha-open-docs; do
  curl -fsS -X POST "$API/lab/$slug/reset" >/dev/null
done

attach() { curl -fsS -X POST "$API/portals" -H 'content-type: application/json' -d "{\"url\": \"$1\"}" >/dev/null; echo "  attached $1"; }
echo "Attaching demo portals"
attach "https://vergabe.vmstart.de/NetServer/PublicationSearchControllerServlet?function=SearchPublications&Gesetzesgrundlage=All&Category=InvitationToTender&thContext=publications"
attach "https://marches.maximilien.fr/?page=Entreprise.EntrepriseAdvancedSearch&AllCons"
attach "https://www.tenderned.nl/aankondigingen/overzicht"
if [ "$EXT" = "--extended" ]; then
  attach "https://www.etenders.gov.ie/epps/quickSearchAction.do?searchType=cftFTS&latest=true"
  attach "$API/lab/multistep/"
  attach "$API/lab/captcha-open-docs/"
fi
attach "$API/lab/ssr/"
attach "$API/lab/login/"
echo "Done. Open $API/"
