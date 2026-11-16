"""Read-only boundary assessment. Never converts result counts into invented scores."""
GROUPS=('ability','recent','distance','course','going','pace','draw','weight_rider')
def assess(bundle):
    if not isinstance(bundle,dict) or bundle.get('schema')!='identity-history-staging-v1':raise ValueError('bundle_schema')
    if bundle.get('model_ready') is not False or bundle.get('production_ready') is not False:raise ValueError('unreviewed_promotion')
    return dict(state='held_for_real_model_input',conditional_history_rows=len(bundle['candidates']),
        matched_profiles=bundle['identity_matches'],missing=['confirmed_target_card','target_card_identity','reviewed_feature_extraction_for_8_groups','trained_and_calibrated_real_model'],
        feature_groups=list(GROUPS),scores_inferred_from_results=False,synthetic_flag_added=False,
        model_ready=False,prediction_executed=False,order_created=False)
