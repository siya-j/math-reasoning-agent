import Mathlib

import Mathlib.Data.Matrix.Notation
import Mathlib.LinearAlgebra.Matrix.Determinant.Basic

open Matrix

theorem mra_goal : (!![1, 2, 3; 4, 5, 6; 7, 8, 9] : Matrix (Fin 3) (Fin 3) ℤ).det = 0 := by
  sorry
