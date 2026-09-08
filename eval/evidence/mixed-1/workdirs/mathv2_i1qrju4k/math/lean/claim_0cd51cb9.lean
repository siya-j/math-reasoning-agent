import Mathlib

import Mathlib.Topology.Basic
import Mathlib.Data.Real.Basic

theorem mra_goal {X : Type*} [TopologicalSpace X] {s : Set X}
    (hs : IsCompact s) (hne : s.Nonempty) {f : X → ℝ} (hf : ContinuousOn f s) :
    ∃ x ∈ s, ∀ y ∈ s, f y ≤ f x := by
  sorry
