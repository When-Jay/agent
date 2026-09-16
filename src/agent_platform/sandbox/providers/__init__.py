"""Concrete SandboxProvider implementations (Docker, Kubernetes, ...).

These adapters are the only place where infrastructure SDKs are imported.
Importing this package does not require any infrastructure SDK; each
provider imports its SDK lazily so the sandbox core stays dependency-free.
"""
