"""
grid.py
=======
Boustrophedon (snake) grid waypoint generator.

Row 0 goes left→right, row 1 right→left, etc.
This minimises total travel distance between rows.
"""


def generate_grid(
    rows: int,
    cols: int,
    step_m: float,
    start_x: float = 0.0,
    start_y: float = 0.0,
) -> list[tuple[float, float]]:
    """
    Return ordered (x, y) waypoints for a boustrophedon grid.

    Parameters
    ----------
    rows, cols : grid dimensions
    step_m     : spacing between waypoints in metres
    start_x/y  : position of the first waypoint (takeoff point)
    """
    if rows < 1 or cols < 1:
        raise ValueError(f'Grid must be at least 1×1, got {rows}×{cols}')
    if step_m <= 0:
        raise ValueError(f'step_m must be positive, got {step_m}')

    waypoints: list[tuple[float, float]] = []
    for row in range(rows):
        col_range = range(cols) if row % 2 == 0 else range(cols - 1, -1, -1)
        for col in col_range:
            x = start_x + col * step_m
            y = start_y + row * step_m
            waypoints.append((round(x, 4), round(y, 4)))
    return waypoints


def describe_grid(rows: int, cols: int, step_m: float) -> str:
    area_m2 = (rows - 1) * (cols - 1) * step_m ** 2 if rows > 1 and cols > 1 else 0
    total_wps = rows * cols
    return (
        f'{rows}×{cols} grid | {total_wps} waypoints | '
        f'{step_m} m spacing | ~{area_m2:.1f} m² coverage'
    )
