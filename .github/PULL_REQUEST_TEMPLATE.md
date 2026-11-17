## Summary

<!-- Briefly describe what this PR changes and why. -->

## Related Issue

<!-- Closes #... -->

## Type of Change

- [ ] Bug fix
- [ ] New `ClassifierDriver` (`quadgate/drivers/`)
- [ ] New `DomainCartridge` (`quadgate/cartridges/`)
- [ ] Harness adapter or MCP enhancement (`quadgate/adapters/`)
- [ ] Benchmarks, calibration, or documentation update

## Verification Checklist

Run the local verification suite from `CONTRIBUTING.md` before requesting review:

- [ ] `pytest -v` passes with zero failures or skipped assertions
- [ ] `mypy --follow-imports=silent quadgate benchmarks` passes cleanly
- [ ] `python -m quadgate.cli doctor` reports `OK` with `< 10 ms` health latency and `clean_shutdown: True`
- [ ] `python -m quadgate.cli bench --dry-replay` completes with exit code `0`
- [ ] Added/updated unit tests cover new behavior (including Stage-2A fail-closed and KV-cache Turn-1 pinning invariants where applicable)
- [ ] Verified diff contains zero API keys, `.env` values, `.db` files, or local user paths
