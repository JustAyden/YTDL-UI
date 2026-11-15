"""Turn Material 3 Expressive spring specs into CSS linear() easing curves.

    python packaging/gen_springs.py   # prints the CSS custom properties used in static/css/app.css

Values from Jetpack Compose's MotionScheme.expressive(): spatial springs overshoot
(bouncy), effects springs are critically damped (no bounce).
"""
import math

SPRINGS = {
    "fast-spatial": (0.6, 800.0),
    "default-spatial": (0.8, 380.0),
    "slow-spatial": (0.8, 200.0),
    "fast-effects": (1.0, 3800.0),
    "default-effects": (1.0, 1600.0),
    "slow-effects": (1.0, 800.0),
}


def position(t, zeta, k):
    w = math.sqrt(k)
    if zeta < 1:
        wd = w * math.sqrt(1 - zeta * zeta)
        return 1 - math.exp(-zeta * w * t) * (math.cos(wd * t) + zeta * w / wd * math.sin(wd * t))
    return 1 - math.exp(-w * t) * (1 + w * t)


def settle_time(zeta, k, eps=0.001):
    t, dt, last_out = 0.0, 0.0005, 0.0
    while t < 5:
        if abs(1 - position(t, zeta, k)) > eps:
            last_out = t
        t += dt
    return last_out + dt


def main():
    for name, (zeta, k) in SPRINGS.items():
        dur = settle_time(zeta, k)
        n = 40
        pts = [position(dur * i / n, zeta, k) for i in range(n + 1)]
        pts[-1] = 1.0
        curve = ", ".join(f"{p:.4f}".rstrip("0").rstrip(".") if p not in (0, 1) else str(int(p)) for p in pts)
        print(f"  --md-spring-{name}: linear({curve});")
        print(f"  --md-spring-{name}-duration: {round(dur * 1000)}ms;")


if __name__ == "__main__":
    main()
