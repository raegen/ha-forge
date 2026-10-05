# Contributing

Read `forge/DOCS.md`, `forge/MODELS.md` and `forge/TESTING.md` before changing behavior. This candidate needs an owner-approved license before external contributions or distribution.

Keep changes small. Preserve per-task permission deadlines, idempotency, Stop & send atomicity, question barriers, serial execution, actual-peer/CSRF enforcement and durable unknown-effect recovery. Never solve an uncertain tool outcome with an automatic retry.

Run tests with temporary files and mocked provider/HA/Supervisor clients. No production credentials, live databases, user recordings or household-device actions belong in a test. Model metadata must come from the fixed OpenAI endpoint; capability profiles need current official documentation and exact-family tests.

Frontend code is local, dependency-free JavaScript/CSS. Keep keyboard navigation, small screens, reduced motion, light/dark modes and pending-submission recovery usable. Use textContent/safe DOM construction for untrusted data. Do not add CDN assets, analytics or inline-script CSP exceptions.

Do not commit diagnostic output, generated reports, `.pyc`, options, context documents, local artifacts, SQLite files or uploads. A change report should distinguish source tests, container build, live provider and physical end-to-end checks.
