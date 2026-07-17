# Product

## Register

product

## Users

The primary audience is the SERES equipment, quality, and manufacturing review team evaluating the competition submission. The operational users represented in the prototype are equipment engineers, process quality engineers, production supervisors, and maintenance technicians responsible for a critical final-assembly tightening station.

Their shared job is to identify equipment-side signals that may become product-quality risks, understand the evidence, decide the next inspection or containment action, and preserve a complete closure record.

## Product Purpose

TorqueGuard is a competition prototype for an AI digital employee that proactively manages equipment-related quality risk. It is intentionally limited to one final-assembly critical tightening station so the full loop can be demonstrated with engineering depth.

The prototype combines layered SPC checks, equipment-health signals, PFMEA and control-plan knowledge, an evidence graph, candidate-cause reasoning, and human-approved task routing. Success means that a reviewer can reproduce a hidden-risk scenario, inspect every piece of evidence behind the risk card, and follow the record through review, action, verification, and closure.

This is not an official SERES system. All equipment, process, and quality records in the repository are synthetic competition data.

## Brand Personality

Restrained, engineering-led, trustworthy.

The interface should feel like a tool that could sit beside a production quality workflow: calm under pressure, explicit about uncertainty, and precise about what is observed versus inferred.

## Anti-references

- A generic AI chat window with no operational workflow.
- Cyberpunk control-room visuals, neon-on-black dashboards, or decorative factory imagery.
- Glossy executive dashboards that surface scores without evidence or action ownership.
- Autonomous-control claims that imply the prototype can stop a line, change PLC parameters, or confirm root cause without an engineer.
- Product pages filled with unsupported performance claims or language that suggests access to internal SERES data.

## Design Principles

1. Evidence before conclusion. Every risk statement must link to a signal, rule, document excerpt, or historical case.
2. One station, end to end. Depth of the tightening scenario matters more than nominal factory-wide coverage.
3. Separate facts from inference. Observations, candidate causes, confidence, and required verification must be visually distinct.
4. Keep engineers in control. The digital employee recommends, routes, records, and follows up; accountable people approve consequential actions.
5. Make the demo reproducible. Synthetic datasets, scenario injection, calculations, and expected outputs remain visible in the repository.

## Accessibility & Inclusion

Target WCAG 2.2 AA. All primary actions must be keyboard accessible; text and interactive controls must meet contrast requirements; color must never be the only risk indicator; charts require textual summaries; motion must respect reduced-motion preferences. The Chinese interface should remain usable at 200% zoom and on common laptop and mobile widths.
