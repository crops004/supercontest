from app.models import Game, Pick
from app.scoring import game_result_against_spread, points_for_pick


def make_game(*, home="KC", away="DEN", home_score=None, away_score=None,
              spread_home=None, spread_away=None):
    g = Game()
    g.home_team = home
    g.away_team = away
    g.final_score_home = home_score
    g.final_score_away = away_score
    g.spread_home = spread_home
    g.spread_away = spread_away
    return g


def make_pick(game, chosen_team):
    p = Pick()
    p.chosen_team = chosen_team
    p.game_id = getattr(game, "id", None)
    return p


def test_not_yet_graded_returns_none():
    g = make_game(home_score=None, away_score=None, spread_home=-3.5)
    assert game_result_against_spread(g) is None


def test_favorite_covers():
    # Home favored by 3.5, wins by 7 -> covers
    g = make_game(home_score=24, away_score=17, spread_home=-3.5, spread_away=3.5)
    assert game_result_against_spread(g) == "home"


def test_favorite_fails_to_cover_underdog_covers():
    # Home favored by 7, wins by only 3 -> away (underdog) covers
    g = make_game(home_score=20, away_score=17, spread_home=-7, spread_away=7)
    assert game_result_against_spread(g) == "away"


def test_push_on_exact_spread():
    # Home favored by 3, wins by exactly 3 -> push
    g = make_game(home_score=20, away_score=17, spread_home=-3, spread_away=3)
    assert game_result_against_spread(g) == "push"


def test_pick_em_tie_is_push():
    g = make_game(home_score=21, away_score=21, spread_home=0, spread_away=0)
    assert game_result_against_spread(g) == "push"


def test_pick_em_outright_winner():
    g = make_game(home_score=24, away_score=20, spread_home=0, spread_away=0)
    assert game_result_against_spread(g) == "home"


def test_derives_missing_spread_side():
    # Only away spread given (away favored by 3); home side must be derived (+3).
    # Away wins by 4, more than the 3-point spread, so away covers.
    g = make_game(home_score=17, away_score=21, spread_home=None, spread_away=-3)
    assert game_result_against_spread(g) == "away"


def test_points_for_pick_win():
    g = make_game(home_score=24, away_score=17, spread_home=-3.5, spread_away=3.5)
    p = make_pick(g, "KC")
    assert points_for_pick(p, g) == 1.0


def test_points_for_pick_loss():
    g = make_game(home_score=24, away_score=17, spread_home=-3.5, spread_away=3.5)
    p = make_pick(g, "DEN")
    assert points_for_pick(p, g) == 0.0


def test_points_for_pick_push():
    g = make_game(home_score=20, away_score=17, spread_home=-3, spread_away=3)
    p = make_pick(g, "KC")
    assert points_for_pick(p, g) == 0.5


def test_points_for_pick_not_graded():
    g = make_game(home_score=None, away_score=None, spread_home=-3)
    p = make_pick(g, "KC")
    assert points_for_pick(p, g) is None


def test_points_for_pick_unknown_team_counts_as_loss():
    g = make_game(home_score=24, away_score=17, spread_home=-3.5, spread_away=3.5)
    p = make_pick(g, "SomeOtherTeam")
    assert points_for_pick(p, g) == 0.0
