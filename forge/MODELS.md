# Models and reasoning effort

Capability review: **October 3, 2026**. Implementation: `forge_models.py`.

## Live account discovery, documented capabilities

Forge makes an authenticated, server-side `GET https://api.openai.com/v1/models` using the configured API key. The UI receives sanitized model IDs, availability metadata and reviewed capability profiles, never the key or account-owner fields. Refreshing the list sends no prompt, files, project context or inference request.

OpenAI's models endpoint reports IDs and basic availability information, not supported reasoning levels or hosted-tool capabilities. Forge therefore intersects the live inventory with a conservative, versioned compatibility table. It does **not** infer support from an arbitrary model prefix, scrape documentation at runtime or present guessed effort levels as API data.

Only exact aliases and their dated snapshots are recognized. Audio, embedding, realtime, search-only, specialized Pro/Codex and unknown models are not automatically offered. A newly released model needs a reviewed profile and regression tests before the selector can safely offer it. The inventory count includes excluded IDs; the compatible count does not.

## Supported selector families

| Family | Reasoning levels offered |
| --- | --- |
| GPT-6 Astra; GPT-6.1 Sol | low, medium, high, xhigh, max |
| GPT-6 Sol/Luna; GPT-5.6 and Sol/Terra/Luna | none, low, medium, high, xhigh, max |
| GPT-5.5; GPT-5.4/mini/nano; GPT-5.2 | none, low, medium, high, xhigh |
| GPT-5.1 | none, low, medium, high |
| GPT-5/mini/nano | minimal, low, medium, high |
| o3; o4-mini | low, medium, high |
| GPT-4.1/mini/nano; GPT-4o/mini | not applicable; reasoning omitted |

The native select control deliberately presents named discrete levels instead of a misleading continuous slider. When changing models, a compatible effort is retained; otherwise the UI visibly chooses the reviewed default (`medium`, or not applicable). No task starts as a result.

Profiles also suppress unsupported request fields. Non-reasoning models omit reasoning and verbosity controls; older profiles omit automatic compaction. Nano profiles conservatively omit hosted web search/code interpreter. GPT-5 minimal effort omits web search. Appliance function tools are retained. Configured web/code options remain the upper bound; the picker cannot enable disabled tools.

## Scope and persistence

- A new conversation starts with app defaults. Draft choices are saved in that browser for that conversation; the last **sent** choice is also stored in the conversation.
- Each Send snapshots `{model, reasoning_effort}` into the task. Every subsequent turn of that task uses the same snapshot.
- Running tasks and explicit Resume keep their original settings. Old pre-0.4 tasks without a snapshot use the configured default; the UI labels this legacy case.
- Retrying an unresolved submission retains its original settings and request ID, files, permission and interruption target. A conflicting reuse is rejected; accepted retries never renew a grant.
- Model choices are **not change authorization**, do not rewrite app options, and do not change household voice-assistant models.
- Changing models preserves `previous_response_id` rather than discarding history. A smaller context window or provider-side cross-model limitation can reject a long conversation. The user may start a new conversation; Forge never deletes history to make it fit.

## Failure behavior

Inventory reads are bounded to 10 seconds, 1 MiB and 5,000 entries. Redirects are refused before forwarding credentials. Successful results cache for five minutes; failed checks back off for one minute. Manual refresh is rate-limited to once per 30 seconds, with single-flight concurrency. Cached rows remain available during an outage and are labelled stale.

Only the exact administrator-configured default is allowed as an explicit offline/unconfirmed fallback. An unknown configured custom model retains the pre-0.4 request contract; this does not imply compatibility. A model listed by the API may still fail because of quota, project permissions, retirement or request incompatibility. Announced past shutdown dates disable a catalog row. There is no automatic model substitution or hidden generation test.

## Maintainer review

For a new family, confirm Responses function tools, image/file inputs, background requests, reasoning values, verbosity, compaction and hosted tools against official documentation. Add an exact profile, table row and positive/negative tests. Review any announced shutdown metadata. Do not enable broad prefix matching or use user-supplied URLs for discovery.

Official sources reviewed:
- https://developers.openai.com/api/reference/resources/models/methods/list
- https://developers.openai.com/api/docs/guides/reasoning
- https://developers.openai.com/api/docs/models/gpt-6-astra
- https://developers.openai.com/api/docs/models/gpt-6.1-sol
- https://developers.openai.com/api/docs/models/gpt-6-sol
- https://developers.openai.com/api/docs/models/gpt-6-luna
- https://developers.openai.com/api/docs/models/gpt-5.6
- https://developers.openai.com/api/docs/models/gpt-5.5
- https://developers.openai.com/api/docs/models/gpt-5.4
- https://developers.openai.com/api/docs/models/gpt-5.1
- https://developers.openai.com/api/docs/models/gpt-5
- https://developers.openai.com/api/docs/models/gpt-4.1-mini
- https://developers.openai.com/api/docs/models/gpt-4o-mini
- https://developers.openai.com/api/docs/guides/tools-web-search
- https://developers.openai.com/api/docs/guides/tools-code-interpreter
- https://developers.openai.com/api/docs/guides/compaction

Live model discovery and isolated fixture tests are not proof that every combination generates successfully. Verify a harmless task through real Ingress after activation; avoid changing devices just to test a selector.
