# Mowazaf Agent Configuration

This directory holds tenant-specific agent configurations (personality,
skills, policies). Each tenant gets a subdirectory.

## Structure

```
skills/
├── sara/          # Default clinic agent
│   ├── persona.md
│   ├── skills/
│   └── policies.md
├── tariq/          # Default e-commerce agent
│   ├── persona.md
│   ├── skills/
│   └── policies.md
└── README.md
```

The Hermes runtime loads the appropriate agent config based on tenant_id.
See app/hermes_client.py `dispatch_to_agent()` for integration.