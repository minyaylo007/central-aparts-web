# Apple Pay / Google Pay on-site via monobank acquiring — research (2026-06-10)

## Question
Can we show Apple Pay / Google Pay buttons directly on www.central-aparts.store
(instead of redirecting to mono hosted checkout), charging via monobank?

## Findings (monobank official API docs)

### Option 1 — Hosted checkout (CURRENT, already live)
`POST /api/merchant/invoice/create` → `pageUrl` → redirect.
The mono "plata by mono" page **already shows Apple Pay and Google Pay buttons**
natively (Safari/Chrome detect wallet support). Zero work, zero certificates,
mono handles all PCI scope. This is what the backoffice does today.

### Option 2 — On-site Apple Pay (host-to-host)
Requirements per mono docs (docs--apple-pay):
- Apple Developer Program account ($99/yr), Account Holder/Admin role
- Create Apple **Merchant ID**; register + verify domain www.central-aparts.store
- Two certificates: Payment Processing cert (.p12, ECDSA-256, 25-month validity,
  shared with mono) + Merchant Identity cert (TLS to Apple servers)
- Apple Pay JS on frontend (Safari only) + backend merchant-session validation endpoint
- Encrypted token → backoffice → mono `POST /api/merchant/wallet/payment` (`aToken`)
- All server work lives in the BACKOFFICE repo (owns the mono merchant token)

### Option 3 — On-site Google Pay (gateway model)
Requirements per mono docs (docs--google-pay):
- Register in Google Pay Business Console → `gatewayMerchantId`; gateway: "monobank"
- Google Pay JS button → encrypted `gToken` → backoffice → mono
  `POST /api/merchant/wallet/payment` (`gToken`, JSON-stringified)
- **No test tokens supported — testing only with production data/cards**
- Backoffice changes required

## Verdict
- On-site wallets are FEASIBLE but are a multi-week backoffice project with
  Apple/Google merchant registration, certificate lifecycle (renew every ~2 yrs),
  Safari-only Apple Pay JS, and prod-only Google Pay testing.
- Industry practice for small direct-booking sites (incl. most boutique hotels
  in EU) = hosted checkout where wallets already work. On-site wallet buttons
  are an Airbnb/Booking-scale optimization.
- Recommendation: keep hosted mono page (wallets already work there TODAY);
  revisit on-site wallets only if funnel data shows drop-off at the redirect.

## Sources
- https://monobank.ua/en/api-docs/acquiring/methods/ia/docs--apple-pay
- https://monobank.ua/en/api-docs/acquiring/methods/ia/docs--google-pay
- https://monobank.ua/en/api-docs/acquiring/extras/tokens/post--api--merchant--wallet--payment
- https://monobank.ua/en/api-docs/acquiring/methods/ia/post--api--merchant--invoice--create
