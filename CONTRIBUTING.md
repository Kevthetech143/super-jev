# Contributing

Use Node.js 24+. Run `npm test` and both documented demo commands before submitting a pull request.

Keep the core independent of business domains. Put domain rules and integrations in separate packs. New tools must validate inputs, describe their side effects, honor cancellation where possible, and explain how uncertain outcomes are reconciled.

Include a regression test for behavior changes. Never commit credentials, private traces, customer data, or generated run logs. Avoid claims about speed, accuracy, or profitability without reproducible measurements.

This project uses Node's native TypeScript stripping. Avoid enums, parameter properties, and other TypeScript features that require code generation. No build toolchain is currently required.
