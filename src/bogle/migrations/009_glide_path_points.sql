-- 009_glide_path_points: target weights that follow the user's age (issue #78).
--
-- The glide path is a handful of anchor ages, each with the target of every
-- ticker in it; between two anchors the targets are interpolated, and before
-- the first or after the last they stay put (src/bogle/glide_path.py). Once a
-- year, on 01/01, the targets of that year's age are written to
-- ``assets.target_weight`` — the column every other part of the app reads.
--
-- The weights of each age add up to exactly 1. A CHECK cannot see other rows,
-- so that rule lives in the code that writes the points and is checked again by
-- the code that reads them. NUMERIC(5,4) is the precision of
-- ``assets.target_weight``, where the interpolated values end up.
--
-- No ON DELETE: an asset that is part of the trajectory cannot be removed
-- without taking it out of the trajectory first.

CREATE TABLE glide_path_points (
    age           SMALLINT      NOT NULL CHECK (age >= 0 AND age <= 150),
    ticker        TEXT          NOT NULL REFERENCES assets (ticker),
    target_weight NUMERIC(5, 4) NOT NULL CHECK (target_weight >= 0 AND target_weight <= 1),
    PRIMARY KEY (age, ticker)
);
