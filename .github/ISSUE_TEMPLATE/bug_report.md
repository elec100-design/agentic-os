---
name: Bug report
about: Something isn't working
title: ""
labels: bug
---

**Describe the bug**
A clear description of what went wrong.

**Environment**
- OS: <!-- macOS 14.x / Ubuntu 22.04 / ... -->
- Python version: <!-- python3 --version -->
- AI CLI(s) in use and version: <!-- e.g. claude --version -->

**Setup status**
If the dashboard loaded at all, paste the output of:
```bash
curl http://localhost:8899/api/setup/status
```

**Steps to reproduce**
1. ...
2. ...

**Expected vs actual behavior**

**Logs**
If running via `install.sh`/launchd, attach relevant lines from
`data/aos.err.log`. If running manually, paste the terminal output.
