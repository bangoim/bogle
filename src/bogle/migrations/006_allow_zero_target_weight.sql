-- 006_allow_zero_target_weight: target_weight >= 0 instead of > 0.
--
-- Zero became a meaning the app needs: "this asset is not part of the plan
-- anymore". A sale that empties a position clears the target with it
-- (src/bogle/closeout.py), because the contribution engine now funds any target
-- without a position — and a target left over from an asset the user walked away
-- from would quietly take the next contribution.
--
-- The upper bound and the SUM(target_weight) <= 1 invariant are untouched: this
-- only stops the floor from rejecting a weight of nothing.

ALTER TABLE assets
    DROP CONSTRAINT assets_target_weight_check;

ALTER TABLE assets
    ADD CONSTRAINT assets_target_weight_check
    CHECK (target_weight >= 0 AND target_weight <= 1);
