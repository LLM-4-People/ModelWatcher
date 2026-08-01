"""Random prompt generation from word banks.

Builds varied benchmark prompts by sampling adjectives, nouns, verbs, and
prepositions into eight sentence structures, then appending the configured
suffix (a structured essay instruction designed to push output toward the
target token ceiling).
"""

import random

from backend.state import c

_ADJ = [
    "ancient", "modern", "forgotten", "emerging", "controversial", "obscure", "fundamental",
    "paradoxical", "practical", "theoretical", "hidden", "overlooked", "disruptive", "elegant",
    "fragile", "resilient", "complex", "deceptive", "beautiful", "dangerous", "subtle",
    "unpredictable", "inevitable", "misunderstood", "revolutionary", "peculiar", "vast",
    "microscopic", "abstract", "concrete", "hypothetical", "ironic", "timeless",
]
_NOUN = [
    "algorithms", "ecosystems", "paradoxes", "metaphors", "catalysts", "thresholds", "anomalies",
    "symmetries", "feedback loops", "trade-offs", "emergence", "entropy", "invariants",
    "boundaries", "convergence", "fractals", "heuristics", "bottlenecks", "equilibrium",
    "cascades", "oscillation", "resonance", "interference", "diffusion", "phase transitions",
    "attractors", "topology", "recursion", "modularity", "redundancy", "homeostasis",
    "scaffolding", "symbiosis", "adaptation", "selection pressure", "drift", "mutation",
    "interpolation", "extrapolation", "decomposition", "abstraction", "composition",
    "polarization", "coupling", "isolation", "propagation", "attenuation", "amplification",
]
_VERB = [
    "explain", "describe", "compare", "analyze", "define", "illustrate", "challenge",
    "reimagine", "deconstruct", "summarize", "evaluate", "trace", "reconcile",
    "contextualize", "differentiate", "critique", "reframe",
]
_PREP = [
    "in the context of", "in relation to", "through the lens of", "as opposed to",
    "in contrast with", "alongside", "at the intersection of", "in spite of",
    "as a precursor to", "as an extension of",
]


def random_prompt() -> str:
    """Generate a random prompt from word banks, suffixed with the configured instruction."""
    adj1, adj2 = random.sample(_ADJ, 2)
    noun1, noun2 = random.sample(_NOUN, 2)
    verb = random.choice(_VERB)
    prep = random.choice(_PREP)
    structures = [
        f"{verb.capitalize()} {adj1} {noun1} {prep} {adj2} {noun2}.",
        f"{verb.capitalize()} {adj2} {noun2} {prep} {adj1} {noun1}.",
        f"How are {adj1} {noun1} related {prep} {adj2} {noun2}? {verb.capitalize()} this.",
        f"Why do {adj1} {noun1} matter {prep} {adj2} {noun2}? {verb.capitalize()} why.",
        f"Are {adj1} {noun1} relevant {prep} {adj2} {noun2}? {verb.capitalize()} your reasoning.",
        f"How do {adj1} {noun1} compare {prep} {adj2} {noun2}? {verb.capitalize()} the differences.",
        f"What role do {adj1} {noun1} play {prep} {adj2} {noun2}? {verb.capitalize()} it.",
        f"How might {adj1} {noun1} evolve {prep} {adj2} {noun2}? {verb.capitalize()} the implications.",
    ]
    return random.choice(structures) + c.benchmark_prompt_suffix
