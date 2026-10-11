# Conversation search

Typing preserves quick title/ID matching and the debounced `content=1&depth=5`
request. Quick search is not evidence of absence in a long conversation.

**Search full saved history** is opt-in. Each click scans another page of saved
WebUI user/assistant text in the active profile; live-only rows, unimported
external history, tool output and missing sidecars are not included. This is a
bounded saved-history search, not a canonical cross-source full-text index.
Results include snippets and a message UID or validated raw location. Opening
uses existing canonical transcript hydration, validates the matched text, then
uses the existing message-jump helper. It never installs search data as chat
history; a changed/unavailable target produces a warning instead of a guessed jump.

Each request scans at most 24 sidecars / 8 MiB, with a cooperative two-second
read/scan deadline. Individual files over 8 MiB are skipped and explicitly
reported as incomplete. The deadline does not include existing sidebar metadata
resolution or interrupt a filesystem syscall / bounded JSON decode. Pagination
is keyset-based, with no persistent search cache; the browser retains at most
240 content results. Continuing always requires a click. New queries and cancel
invalidate in-flight responses; profile changes reject stale results. Opening
a result is a separate explicit full-transcript load and uses existing limits.

The saved-file reader does not perform state.db/journal reconciliation; canonical
loading on open may therefore reject an obsolete raw location. New/renamed
sessions during pagination can require restarting search. Errors, cancellation
and incomplete coverage are not presented as a successful empty search.

UI translations for this integration are handed off in the implementation
evidence `search-translations.json` for the locale-owner change.
