from app.models.entities import SensitiveDemographics, UserProfile


def test_demographics_model_is_separate_from_profile_model():
    assert SensitiveDemographics.__tablename__ == "sensitive_demographics"
    assert UserProfile.__tablename__ == "user_profiles"
    assert not hasattr(UserProfile, "gender")

