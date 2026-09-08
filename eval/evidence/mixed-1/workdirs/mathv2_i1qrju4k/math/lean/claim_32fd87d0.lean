import Mathlib

import Mathlib.Topology.Instances.Real
import Mathlib.Topology.ContinuousOn

theorem mra_goal {X : Type*} [TopologicalSpace X] {s : Set X}
    (hs : IsCompact s) (hne : s.Nonempty) {f : X → ℝ} (hf : ContinuousOn f s) :
    ∃ x ∈ s, ∀ y ∈ s, f y ≤ f x := by
  sorry
