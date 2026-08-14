#!/usr/bin/env python3
import aws_cdk as cdk

from mnemos_stack import MnemosStack

app = cdk.App()
MnemosStack(
    app, "MnemosStack",
    description="Mnemos: agentic memory system with conflict resolution - CockroachDB x AWS Hackathon",
)
app.synth()
