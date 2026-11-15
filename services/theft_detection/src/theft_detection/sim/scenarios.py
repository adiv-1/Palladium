"""Scripted scenario library: ordinary interactions (negatives) and staged-style positives.

Every scenario is randomised (subject size, position, speeds, timing, mirroring) from a seed,
so thresholds cannot be fitted to one fixed clip. Negatives N01-N12 correspond one-to-one to
the false-positive list in the project brief; N13-N16 are additional hard cases.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from ..config import ZoneConfig
from .world import FRAME_H, FRAME_W, Anchor, Bag, Label, PathBuilder, Person, Scenario, Seg

SNATCH = "possible_snatching"
REMOVAL = "suspicious_object_removal"
ZONE_ENTRY = "possible_restricted_area_entry"


class _Ctx:
    def __init__(self, rng: np.random.Generator) -> None:
        self.rng = rng
        self.H = float(rng.uniform(170, 290))
        self.y = float(rng.uniform(560, 680))
        self.m = int(rng.choice([-1, 1]))

    def u(self, a: float, b: float) -> float:
        return float(self.rng.uniform(a, b))

    def pick(self, *options):
        return options[int(self.rng.integers(len(options)))]

    def X(self, xu: float) -> float:
        """x position given in body heights from the image centre (mirrored if needed)."""
        return FRAME_W / 2 + self.m * xu * self.H

    @property
    def edge(self) -> float:
        """Distance (body heights) from the centre to just outside the frame."""
        return FRAME_W / 2 / self.H + 0.5

    def path(self, xu: float, dy: float = 0.0, t: float = 0.0) -> PathBuilder:
        return PathBuilder(self.X(xu), self.y + dy * self.H, self.H, t)

    def person(self, pid: str, pb: PathBuilder, color: int) -> Person:
        return Person(pid=pid, height=self.H, path=pb.kf, color=color)

    def held(self, pid: str, side: int = 1, style: str = "side") -> Anchor:
        return Anchor(person=pid, style=style, side=self.m * side)

    def ground(self, xu: float, dy: float = 0.0) -> Anchor:
        return Anchor(pos=(self.X(xu), self.y + dy * self.H - 0.13 * self.H))

    def bag(self, bid: str, segs: list[Seg], label: str = "backpack", color: int = 0,
            size: tuple[float, float] = (0.22, 0.26)) -> Bag:
        return Bag(bid=bid, label=label, segs=segs, size=size, ref_height=self.H, color=color)

    def zone(self, zone_id: str, kind: str, x0: float, x1: float, y0: float, y1: float) -> ZoneConfig:
        if self.m < 0:
            x0, x1 = 1 - x1, 1 - x0
        return ZoneConfig(zone_id=zone_id, kind=kind,
                          polygon=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


# ======================================================================================
# negatives
# ======================================================================================
def n01_handoff(rng) -> Scenario:
    c = _Ctx(rng)
    T = 12.0
    t1, d = c.u(3, 5), c.u(0.9, 1.5)
    a, b = c.path(-0.40), c.path(0.35)
    variant = c.pick("stay", "receiver_leaves", "giver_leaves")
    if variant == "receiver_leaves":
        b.until(t1 + d + c.u(0.5, 1.5)).to(c.X(c.edge + 0.5), c.y, c.u(0.6, 0.95))
    elif variant == "giver_leaves":
        a.until(t1 + d + c.u(0.5, 1.5)).to(c.X(-c.edge - 0.5), c.y, c.u(0.6, 0.95))
    bag = c.bag("bag", [Seg(0, t1, c.held("A", 1)), Seg(t1 + d, T, c.held("B", -1))])
    return Scenario("N01_handoff_to_friend", "negative",
                    "A hands a backpack to B over about a second; they stay or part at walking pace.",
                    T, [c.person("A", a.until(T), 0), c.person("B", b.until(T), 1)], [bag])


def n02_own_pickup(rng) -> Scenario:
    c = _Ctx(rng)
    a = c.path(-c.edge).to(c.X(-0.3), c.y, 0.8)
    t_set = a.t + 0.3
    a.until(t_set + 1.0).to(c.X(c.u(1.2, 1.8)), c.y, 0.7).stay(c.u(4, 6)).to(c.X(-0.25), c.y, 0.7)
    t_pick = a.t + 0.3
    a.until(t_pick + 1.0).to(c.X(-c.edge - 0.5), c.y, 0.8)
    T = a.t
    bag = c.bag("bag", [Seg(0, t_set, c.held("A", 1)), Seg(t_set + 0.6, t_pick, c.ground(0.05)),
                        Seg(t_pick + 0.6, T, c.held("A", 1))])
    return Scenario("N02_picks_up_own_bag", "negative",
                    "A sets a bag down, walks a few steps away while staying in view, returns and takes it.",
                    T, [c.person("A", a, 0)], [bag])


def n03_strap_adjust(rng) -> Scenario:
    c = _Ctx(rng)
    T = 10.0
    a = c.path(-0.8).stay(1.0).to(c.X(0.6), c.y, c.u(0.3, 0.6)).until(T)
    p = c.path(c.edge, 0.06).stay(c.u(0.5, 2.5)).to(c.X(-c.edge - 0.5), c.y + 0.06 * c.H, 0.85).until(T)
    segs, t = [], 0.0
    for _ in range(3):
        t1 = t + c.u(1.5, 2.5)
        segs.append(Seg(t, t1, c.held("A", 1, "back")))
        segs.append(Seg(t1 + 0.3, t1 + 0.3 + c.u(0.4, 0.9), c.held("A", -1, "side")))
        t = segs[-1].t1 + 0.3
    segs.append(Seg(t, T, c.held("A", 1, "back")))
    return Scenario("N03_adjusts_backpack_strap", "negative",
                    "A repeatedly swings a backpack between back and side while someone walks past.",
                    T, [c.person("A", a, 0), c.person("P", p, 2)], [c.bag("bag", segs)])


def n04_exchange(rng) -> Scenario:
    c = _Ctx(rng)
    T = 11.0
    t1, d = c.u(3, 4.5), c.u(1.0, 1.4)
    a, b = c.path(-0.42).until(T), c.path(0.38).until(T)
    bag1 = c.bag("bag1", [Seg(0, t1, c.held("A", -1)), Seg(t1 + d, T, c.held("B", 1))])
    bag2 = c.bag("bag2", [Seg(0, t1 + 0.5, c.held("B", -1)), Seg(t1 + 0.5 + d, T, c.held("A", 1))],
                 label="handbag", color=1)
    return Scenario("N04_exchange_belongings", "negative", "A and B swap bags at a normal pace.",
                    T, [c.person("A", a, 0), c.person("B", b, 1)], [bag1, bag2])


def n05_help_carry(rng) -> Scenario:
    c = _Ctx(rng)
    v = c.u(0.6, 0.8)
    a = c.path(-c.edge + 0.6).to(c.X(c.edge + 0.6), c.y, v)
    b = c.path(-c.edge - 0.1, 0.03).to(c.X(c.edge - 0.1), c.y + 0.03 * c.H, v)
    T = min(a.t, 14.0)
    t1, d = c.u(2.5, 4), c.u(0.8, 1.2)
    bag = c.bag("case", [Seg(0, t1, c.held("A", -1)), Seg(t1 + d, T, c.held("B", 1))],
                label="suitcase", size=(0.26, 0.40))
    return Scenario("N05_helps_carry_luggage", "negative",
                    "Two people walk together; one takes the other's suitcase while walking.",
                    T, [c.person("A", a, 0), c.person("B", b, 1)], [bag])


def n06_move_to_chair(rng) -> Scenario:
    c = _Ctx(rng)
    a = c.path(-c.edge).to(c.X(-0.3), c.y, 0.8)
    t_set = a.t + 0.3
    a.until(t_set + 1.0).to(c.X(1.3), c.y, 0.7)
    persons, variant = [], c.pick("self", "friend")
    wait = c.u(4.5, 6.5)
    if variant == "self":
        a.stay(wait).to(c.X(-0.25), c.y, 0.7)
        t_pick = a.t + 0.3
        a.until(t_pick + 0.8).to(c.X(0.6), c.y, 0.6)
        t_put = a.t + 0.2
        a.until(t_put + 3.0)
        T = a.t
        segs = [Seg(0, t_set, c.held("A", 1)), Seg(t_set + 0.6, t_pick, c.ground(0.05)),
                Seg(t_pick + 0.6, t_put, c.held("A", 1)), Seg(t_put + 0.6, T, c.ground(0.9))]
    else:
        b = c.path(1.75, -0.03).until(a.t + wait).to(c.X(0.35), c.y - 0.03 * c.H, 0.7)
        t_pick = b.t + 0.3
        b.until(t_pick + 0.8).to(c.X(1.0), c.y - 0.03 * c.H, 0.6)
        t_put = b.t + 0.2
        b.until(t_put + 3.0)
        T = b.t
        a.until(T)
        persons.append(c.person("B", b, 1))
        segs = [Seg(0, t_set, c.held("A", 1)), Seg(t_set + 0.6, t_pick, c.ground(0.05)),
                Seg(t_pick + 0.6, t_put, c.held("B", -1)), Seg(t_put + 0.6, T, c.ground(0.85))]
    return Scenario("N06_moves_bag_to_another_seat", "negative",
                    "A resting bag is moved about one body height and set down again, by the person "
                    "who left it or by a companion standing with them.",
                    T, [c.person("A", a.until(T), 0)] + persons, [c.bag("bag", segs)])


def n07_crowd(rng) -> Scenario:
    c = _Ctx(rng)
    T = 14.0
    persons = [c.person("A", c.path(c.u(-0.3, 0.3)).until(T), 0)]
    for i in range(int(rng.integers(4, 7))):
        direction = c.pick(-1, 1)
        dy = c.pick(-1, 1) * c.u(0.03, 0.14)
        speed = c.u(2.0, 2.6) if i == 0 else c.u(0.6, 1.1)
        pb = c.path(-direction * c.edge, dy).stay(c.u(0.3, 8.0))
        pb.to(c.X(direction * (c.edge + 0.5)), c.y + dy * c.H, speed).until(T)
        persons.append(c.person(f"P{i}", pb, 1 + i % 3))
    bag = c.bag("bag", [Seg(0, T, c.held("A", c.pick(-1, 1)))])
    return Scenario("N07_crowd_brushing_past", "negative",
                    "Several people walk and one jogs closely past a person holding a bag.",
                    T, persons, [bag])


def n08_occlusion(rng) -> Scenario:
    c = _Ctx(rng)
    variant = c.pick("held", "resting")
    if variant == "held":
        T = 10.0
        a = c.path(c.u(-0.3, 0.3)).until(T)
        speed = c.pick(c.u(0.7, 1.0), c.u(2.0, 2.8))
        b = c.path(-c.edge, 0.10).stay(c.u(1.5, 3.5)).to(c.X(c.edge + 0.5), c.y + 0.10 * c.H, speed).until(T)
        bags = [c.bag("bag", [Seg(0, T, c.held("A", c.pick(-1, 1)))]),
                c.bag("bag_b", [Seg(0, T, c.held("B", -1, "back"))], color=1)]
        persons = [c.person("A", a, 0), c.person("B", b, 1)]
    else:
        a = c.path(-c.edge).to(c.X(-0.3), c.y, 0.8)
        t_set = a.t + 0.3
        a.until(t_set + 1.0).to(c.X(-1.6), c.y, 0.7)
        cpb = c.path(c.edge, 0.12).until(a.t + 4.0).to(c.X(0.08), c.y + 0.12 * c.H, 0.8)
        cpb.stay(c.u(3, 5)).to(c.X(-c.edge - 0.5), c.y + 0.12 * c.H, 0.8)
        T = cpb.t + 3.5
        bags = [c.bag("bag", [Seg(0, t_set, c.held("A", 1)), Seg(t_set + 0.6, T, c.ground(0.05))])]
        persons = [c.person("A", a.until(T), 0), c.person("C", cpb.until(T), 2)]
    return Scenario("N08_temporary_occlusion", "negative",
                    "A bag disappears behind a passer-by (or someone who stops in front of it) and reappears.",
                    T, persons, bags)


def n09_drop_and_return(rng) -> Scenario:
    c = _Ctx(rng)
    a = c.path(-c.edge + 0.4).to(c.X(-0.3), c.y, 0.8)
    t_drop = a.t
    a.to(c.X(0.9), c.y, 0.8)
    b = c.path(-c.edge - 0.8, 0.02).to(c.X(-0.55), c.y + 0.02 * c.H, 0.8)
    t_pick = b.t + 0.2
    b.until(t_pick + 0.7).to(c.X(0.2), c.y + 0.02 * c.H, 0.8)
    t_give = b.t + 0.3
    T = t_give + 4.0
    bag = c.bag("bag", [Seg(0, t_drop, c.held("A", -1)), Seg(t_drop + 0.35, t_pick, c.ground(-0.3)),
                        Seg(t_pick + 0.5, t_give, c.held("B", 1)), Seg(t_give + 1.0, T, c.held("A", -1))],
                label="handbag")
    return Scenario("N09_dropped_item_returned", "negative",
                    "A drops a bag while walking; the person behind picks it up and hands it back.",
                    T, [c.person("A", a.until(T), 0), c.person("B", b.until(T), 1)], [bag])


def n10_runner_own_bag(rng) -> Scenario:
    c = _Ctx(rng)
    v = c.u(2.5, 3.5)
    r = c.path(-c.edge).stay(c.u(1.0, 2.0)).to(c.X(c.edge + 0.5), c.y, v)
    T = r.t + 3.0
    dy = c.pick(-1, 1) * c.u(0.05, 0.12)
    b = c.path(c.u(-0.3, 0.3), dy).until(T)
    bags = [c.bag("bag", [Seg(0, T, c.held("R", c.pick(-1, 1), c.pick("back", "side")))]),
            c.bag("bag_b", [Seg(0, T, c.held("B", c.pick(-1, 1)))], label="handbag", color=1)]
    return Scenario("N10_runs_with_own_bag", "negative",
                    "Someone runs through the scene carrying their own bag, passing close to a bystander with a bag.",
                    T, [c.person("R", r.until(T), 0), c.person("B", b, 1)], bags)


def n11_camera_shake(rng) -> Scenario:
    c = _Ctx(rng)
    T = 10.0
    a, b = c.path(-0.40).until(T), c.path(0.35).until(T)
    segs = [Seg(0, T, c.held("A", 1))]
    if c.pick(True, False):
        t1, d = c.u(3.0, 4.5), c.u(1.0, 1.4)
        segs = [Seg(0, t1, c.held("A", 1)), Seg(t1 + d, T, c.held("B", -1))]
    return Scenario("N11_camera_shake", "negative",
                    "The camera shakes for several seconds while two people stand together (sometimes handing over a bag).",
                    T, [c.person("A", a, 0), c.person("B", b, 1)], [c.bag("bag", segs)],
                    shake=(c.u(15, 45), 2.0, c.u(5.0, 7.0)), shake_seed=int(rng.integers(1, 100)))


def n12_authorized_removal(rng) -> Scenario:
    c = _Ctx(rng)
    p = c.path(-c.edge).stay(c.u(3.5, 5.0)).to(c.X(-0.3), c.y, 0.8)
    t_pick = p.t + 0.3
    p.until(t_pick + 0.9).to(c.X(c.edge + 0.6), c.y, c.u(0.8, 2.6))
    T = p.t + 1.0
    bag = c.bag("bag", [Seg(0, t_pick, c.ground(0.0)), Seg(t_pick + 0.5, T, c.held("P", 1))])
    return Scenario("N12_removal_from_unmonitored_spot", "negative",
                    "Someone collects a bag from a place with no configured access rule; nobody else is in view.",
                    T, [c.person("P", p.until(T), 0)], [bag])


def n13_crossing_walkers(rng) -> Scenario:
    c = _Ctx(rng)
    p = c.path(-c.edge).to(c.X(c.edge + 0.5), c.y, c.u(0.7, 0.95))
    q = c.path(c.edge, 0.04).to(c.X(-c.edge - 0.5), c.y + 0.04 * c.H, c.u(0.7, 0.95))
    T = max(p.t, q.t)
    bag = c.bag("bag", [Seg(0, T, c.held("P", c.pick(-1, 1)))])
    return Scenario("N13_two_walkers_cross", "negative",
                    "Two people walk past each other at close range, one carrying a bag (track-swap stress test).",
                    T, [c.person("P", p.until(T), 0), c.person("Q", q.until(T), 1)], [bag])


def n14_zone_near_miss(rng) -> Scenario:
    c = _Ctx(rng)
    zone = c.zone("staff_only", "restricted", 0.62, 0.95, 0.5, 1.0)
    edge_u = (0.62 * FRAME_W - FRAME_W / 2) / c.H     # zone edge in body heights from centre
    p = c.path(-1.0).stay(1.0)
    if c.pick(True, False):
        p.to(c.X(edge_u - 0.25), c.y, 0.8).stay(3.0)          # box overlaps the zone, feet do not
    else:
        p.to(c.X(edge_u + 0.2), c.y, 0.8).stay(c.u(0.1, 0.4)) # steps in for well under a second
    p.to(c.X(-1.5), c.y, 0.8).stay(2.0)
    return Scenario("N14_passes_restricted_zone_edge", "negative",
                    "A person stops beside a restricted zone or steps inside for a fraction of a second.",
                    p.t, [c.person("P", p, 0)], [], zones=[zone])


def n15_owner_returns_from_out_of_view(rng) -> Scenario:
    c = _Ctx(rng)
    a = c.path(-c.edge).to(c.X(-0.3), c.y, 0.8)
    t_set = a.t + 0.3
    a.until(t_set + 1.0).to(c.X(-c.edge - 1.0), c.y, 0.8).stay(c.u(5, 8)).to(c.X(-0.3), c.y, 0.8)
    t_pick = a.t + 0.3
    a.until(t_pick + 0.9).to(c.X(c.edge + 0.6), c.y, 0.8)
    T = a.t
    bag = c.bag("bag", [Seg(0, t_set, c.held("A", 1)), Seg(t_set + 0.6, t_pick, c.ground(0.05)),
                        Seg(t_pick + 0.6, T, c.held("A", 1))])
    return Scenario("N15_owner_returns_from_out_of_view", "negative",
                    "A leaves a bag, walks out of view, and comes back for it (necessarily under a new track ID).",
                    T, [c.person("A", a, 0)], [bag])


def n16_runs_after_crossing(rng) -> Scenario:
    c = _Ctx(rng)
    wait, vq = c.u(1.0, 2.0), c.u(0.7, 1.0)
    q = c.path(-c.edge, 0.05).stay(wait).to(c.X(c.edge + 0.5), c.y + 0.05 * c.H, vq)
    t_cross = wait + c.edge / vq               # moment the walker is level with the bag holder
    p = c.path(0.0).until(t_cross + c.u(0.2, 0.6)).to(c.X(-c.edge - 0.5), c.y, c.u(2.3, 3.0))
    T = max(p.t, q.t)
    bag = c.bag("bag", [Seg(0, T, c.held("P", c.pick(-1, 1)))])
    return Scenario("N16_runs_off_right_after_being_passed", "negative",
                    "A bag holder is passed closely by a walker and immediately runs off with their own bag "
                    "(deliberately adversarial: a track swap here mimics a transfer).",
                    T, [c.person("P", p.until(T), 0), c.person("Q", q.until(T), 1)], [bag])


# ======================================================================================
# positives (staged-style)
# ======================================================================================
def p01_runby_snatch(rng) -> Scenario:
    c = _Ctx(rng)
    xa, va = c.u(-0.9, -0.3), c.pick(0.0, 0.0, c.u(0.3, 0.5))
    vb = c.u(2.2, 3.2)
    lead = c.u(1.5, 2.5)                       # runner waits off-screen first
    xb0 = -c.edge
    t_g = lead + (xa + va * lead - xb0) / (vb - va)
    dy = c.pick(-1, 1) * c.u(0.04, 0.14)
    b = c.path(xb0, dy).stay(lead).to(c.X(c.edge + 1.0), c.y + dy * c.H, vb)
    a = c.path(xa)
    if va > 0:
        a.to(c.X(xa + va * t_g), c.y, va)
    a.until(t_g)
    chase = c.pick(False, True)
    if chase:
        a.stay(c.u(0.5, 0.9)).to(c.X(c.edge + 1.0), c.y, c.u(1.8, 2.4))
    T = b.t + 2.0
    bag = c.bag("bag", [Seg(0, t_g, c.held("A", c.pick(-1, 1))),
                        Seg(t_g + c.u(0.15, 0.35), T, c.held("B", c.pick(-1, 1)))],
                label=c.pick("backpack", "handbag"))
    return Scenario("P01_runby_snatch", "positive",
                    "Staged: a runner grabs a bag from a standing or slowly walking person and keeps running"
                    + (" while the person gives chase." if chase else "."),
                    T, [c.person("A", a.until(T), 0), c.person("B", b.until(T), 1)], [bag],
                    labels=[Label(SNATCH, t_g)])


def p02_walkup_yank(rng) -> Scenario:
    c = _Ctx(rng)
    xa = c.u(-0.6, 0.0)
    b = c.path(c.edge).stay(c.u(1.0, 2.0)).to(c.X(xa + 0.55), c.y, 0.9).stay(c.u(0.2, 1.5))
    t_g = b.t
    away = c.pick(1, -1) if xa > -0.3 else 1
    b.stay(0.15).to(c.X(away * (c.edge + 1.0)), c.y + (0.08 * c.H if away < 0 else 0), c.u(2.3, 3.2))
    T = b.t + 2.0
    a = c.path(xa).until(T)
    bag = c.bag("bag", [Seg(0, t_g, c.held("A", 1)), Seg(t_g + c.u(0.2, 0.4), T, c.held("B", c.pick(-1, 1)))],
                label=c.pick("backpack", "handbag"))
    return Scenario("P02_walkup_and_pull_away", "positive",
                    "Staged: someone walks up to a standing person, pulls the bag from their side and runs off.",
                    T, [c.person("A", a, 0), c.person("B", b.until(T), 1)], [bag],
                    labels=[Label(SNATCH, t_g)])


def p03_unattended_removal(rng) -> Scenario:
    c = _Ctx(rng)
    a = c.path(-c.edge).to(c.X(-0.2), c.y, 0.8)
    t_set = a.t + 0.3
    a.until(t_set + 1.0).to(c.X(-c.u(1.5, 1.9)), c.y, 0.7)
    b = c.path(c.edge, 0.02).until(t_set + 0.6 + c.u(4.5, 7.0)).to(c.X(0.42), c.y + 0.02 * c.H, 0.9)
    t_pick = b.t + 0.2
    b.until(t_pick + c.u(0.5, 0.8)).to(c.X(c.edge + 1.0), c.y + 0.02 * c.H, c.u(1.0, 2.8))
    T = b.t + 2.0
    bag = c.bag("bag", [Seg(0, t_set, c.held("A", 1)), Seg(t_set + 0.6, t_pick, c.ground(0.12)),
                        Seg(t_pick + c.u(0.4, 0.6), T, c.held("B", -1))])
    return Scenario("P03_unattended_bag_taken_while_depositor_in_view", "positive",
                    "Staged: A sets a bag down and stands a few steps away; a different person picks it up and leaves.",
                    T, [c.person("A", a.until(T), 0), c.person("B", b.until(T), 1)], [bag],
                    labels=[Label(REMOVAL, t_pick)])


def p04_monitored_zone_removal(rng) -> Scenario:
    c = _Ctx(rng)
    zone = c.zone("bag_rack", "monitored", 0.40, 0.60, 0.55, 1.0)
    b = c.path(-c.edge).stay(c.u(3.5, 5.0)).to(c.X(-0.3), c.y, 0.8)
    t_pick = b.t + 0.3
    b.until(t_pick + 0.9).to(c.X(c.edge + 0.8), c.y, c.u(0.8, 2.5))
    T = b.t + 1.5
    bag = c.bag("bag", [Seg(0, t_pick, c.ground(0.0)), Seg(t_pick + 0.5, T, c.held("B", 1))])
    return Scenario("P04_removal_from_monitored_zone", "positive",
                    "Staged: a bag resting inside an explicitly configured monitored zone is taken away.",
                    T, [c.person("B", b.until(T), 0)], [bag], labels=[Label(REMOVAL, t_pick)], zones=[zone])


def p05_restricted_entry(rng) -> Scenario:
    c = _Ctx(rng)
    zone = c.zone("staff_only", "restricted", 0.62, 0.95, 0.5, 1.0)
    edge_u = (0.62 * FRAME_W - FRAME_W / 2) / c.H
    target_u = (0.78 * FRAME_W - FRAME_W / 2) / c.H
    p = c.path(-1.0).stay(c.u(1.0, 2.0)).to(c.X(edge_u), c.y, 0.8)
    t_enter = p.t
    p.to(c.X(target_u), c.y, 0.8).stay(c.u(2.0, 4.0)).to(c.X(-1.0), c.y, 0.8)
    return Scenario("P05_restricted_zone_entry", "positive",
                    "Staged: a person walks into a configured restricted zone and stays for a few seconds.",
                    p.t, [c.person("P", p, 0)], [], labels=[Label(ZONE_ENTRY, t_enter)], zones=[zone])


# --------------------------------------------------------------------------------------
# Hidden-exchange cases (added with the unobserved-transfer verifier; defined before it was run).
# The bag is out of sight for 0.8-2.0 s while the two people are in contact.
# --------------------------------------------------------------------------------------
def _hidden_exchange(rng, depart_speed: float | None, kind: str, label: str | None, description: str) -> Scenario:
    c = _Ctx(rng)
    xa = c.u(-0.5, 0.0)
    contact = c.u(0.8, 2.0)                                  # how long the bag stays out of sight
    b = c.path(c.edge).stay(c.u(1.0, 2.0)).to(c.X(xa + 0.5), c.y + 0.03 * c.H, 0.9)
    t_c = b.t
    b.stay(contact)
    if depart_speed is not None:
        b.to(c.X(c.edge + 1.0), c.y + 0.03 * c.H, depart_speed)
    T = max(b.t, t_c + contact) + 3.0
    a = c.path(xa).until(T)
    bag = c.bag("bag", [Seg(0, t_c, c.held("A", 1)), Seg(t_c + 0.4, T, c.held("B", -1))],
                label=c.pick("backpack", "handbag"))
    bag.hidden = [(t_c - 0.1, t_c + contact)]
    return Scenario(f"{kind}", "positive" if label else "negative", description, T,
                    [c.person("A", a, 0), c.person("B", b.until(T), 1)], [bag],
                    labels=[Label(label, t_c)] if label else [])


def p06_snatch_bag_hidden(rng) -> Scenario:
    return _hidden_exchange(rng, float(rng.uniform(2.2, 3.2)), "P06_snatch_with_bag_hidden_during_grab", SNATCH,
                            "Staged: someone walks up, the bag disappears between the two bodies for 0.8-2 s, "
                            "then the other person runs off with it.")


def n17_hidden_handoff_walk(rng) -> Scenario:
    return _hidden_exchange(rng, float(rng.uniform(0.6, 0.95)), "N17_hidden_handoff_then_walks_away", None,
                            "A hands the bag over between their bodies (not visible); the receiver then walks away.")


def n18_hidden_handoff_stay(rng) -> Scenario:
    return _hidden_exchange(rng, None, "N18_hidden_handoff_both_stay", None,
                            "A hands the bag over between their bodies (not visible); both stay together.")


NEGATIVES: list[Callable[[np.random.Generator], Scenario]] = [
    n01_handoff, n02_own_pickup, n03_strap_adjust, n04_exchange, n05_help_carry, n06_move_to_chair,
    n07_crowd, n08_occlusion, n09_drop_and_return, n10_runner_own_bag, n11_camera_shake,
    n12_authorized_removal, n13_crossing_walkers, n14_zone_near_miss,
    n15_owner_returns_from_out_of_view, n16_runs_after_crossing,
    n17_hidden_handoff_walk, n18_hidden_handoff_stay,
]
POSITIVES: list[Callable[[np.random.Generator], Scenario]] = [
    p01_runby_snatch, p02_walkup_yank, p03_unattended_removal, p04_monitored_zone_removal,
    p05_restricted_entry, p06_snatch_bag_hidden,
]
ALL = NEGATIVES + POSITIVES
BY_NAME: dict[str, Callable[[np.random.Generator], Scenario]] = {
    fn(np.random.default_rng(0)).name: fn for fn in ALL
}


def build(name: str, seed: int = 0) -> Scenario:
    return BY_NAME[name](np.random.default_rng(seed))
