"""Small, schema-aware synthetic inputs for the pinned upstream graders.

These exercise real metric code, not competition data or model quality.
Return (submission, answers, expected_score); None means check finiteness only.
"""
import ast
import inspect

import numpy as np
import pandas as pd


def pair(data, expected=1.0):
    answers = pd.DataFrame(data)
    return answers.copy(deep=True), answers, expected


def onehot(id_column, classes):
    classes = list(classes)
    count = len(classes)
    frame = pd.DataFrame(np.eye(count), columns=classes)
    frame.insert(0, id_column, [str(i) for i in range(count)])
    return frame.copy(), frame, 0.0


def fixtures(competition, module):
    name = competition.id
    tree = ast.parse(inspect.getsource(module))
    # Common official metric adapters explicitly declare their input columns.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id not in ['prepare_for_accuracy_metric', 'prepare_for_auroc_metric']:
            continue
        values = {kw.arg: kw.value.value for kw in node.keywords if isinstance(kw.value, ast.Constant)}
        id_column = values.get('id_col', values.get('id_column'))
        target = values.get('target_col', values.get('target_column'))
        if id_column and target:
            return pair({id_column: [str(i) for i in range(8)], target: [0, 1] * 4})

    categorical = {
        'aptos2019-blindness-detection': ('id_code', 'diagnosis'),
        'learning-agency-lab-automated-essay-scoring-2': ('essay_id', 'score'),
        'herbarium-2020-fgvc7': ('Id', 'Predicted'),
        'herbarium-2021-fgvc8': ('Id', 'Predicted'),
        'herbarium-2022-fgvc9': ('Id', 'Predicted'),
        'iwildcam-2019-fgvc6': ('Id', 'Category'),
        'paddy-disease-classification': ('image_id', 'label'),
        'plant-seedlings-classification': ('file', 'species'),
        'nfl-player-contact-detection': ('contact_id', 'contact'),
    }
    if name in categorical:
        identifier, target = categorical[name]
        labels = [0, 1, 0, 1] if name == 'nfl-player-contact-detection' else [1, 2, 1, 2]
        return pair({identifier: ['a', 'b', 'c', 'd'], target: labels})
    if name.startswith('text-normalization-challenge-'):
        return pair(dict(id=['a', 'b'], after=['one', 'two']))
    if name == 'inaturalist-2019-fgvc6':
        return pair(dict(id=['a', 'b'], predicted=['1', '2']), 0.)
    if name == 'AI4Code':
        return pair(dict(id=['a', 'b'], cell_order=['c1 c2 c3', 'd1 d2']))
    if name == 'alaska2-image-steganalysis':
        return pair(dict(Id=['a', 'b', 'c', 'd'], Label=[0, 1, 0, 1]))
    if name in ['billion-word-imputation', 'bms-molecular-translation', 'multi-modal-gesture-recognition']:
        identifier, target, texts = {
            'billion-word-imputation': ('id', 'sentence', ['hello world', 'some text']),
            'bms-molecular-translation': ('image_id', 'InChI', ['InChI=1S/H2O/h1H2', 'InChI=1S/CH4/h1H4']),
            'multi-modal-gesture-recognition': ('Id', 'Sequence', ['1 2 3', '2 1']),
        }[name]
        return pair({identifier: ['a', 'b'], target: texts}, 0.)
    if name in ['dog-breed-identification', 'leaf-classification', 'spooky-author-identification']:
        return onehot('id', module.DOGS_LIST if name == 'dog-breed-identification' else module.CLASSES)
    if name == 'lmsys-chatbot-arena':
        return onehot('id', ['winner_model_a', 'winner_model_b', 'winner_tie'])
    if name in ['dogs-vs-cats-redux-kernels-edition', 'statoil-iceberg-classifier-challenge']:
        return pair({'id': ['a', 'b'], 'label' if name.startswith('dogs-') else 'is_iceberg': [0, 1]}, 0.)
    if name in ['chaii-hindi-and-tamil-question-answering', 'tweet-sentiment-extraction',
                'facebook-recruiting-iii-keyword-extraction', 'imet-2020-fgvc7',
                'plant-pathology-2021-fgvc8']:
        identifier, target = {
            'chaii-hindi-and-tamil-question-answering': ('id', 'PredictionString'),
            'tweet-sentiment-extraction': ('textID', 'selected_text'),
            'facebook-recruiting-iii-keyword-extraction': ('Id', 'Tags'),
            'imet-2020-fgvc7': ('id', 'attribute_ids'),
            'plant-pathology-2021-fgvc8': ('image', 'labels'),
        }[name]
        return pair({identifier: ['a', 'b'], target: ['1 2', '2 3']})
    if name in ['h-and-m-personalized-fashion-recommendations', 'whale-categorization-playground',
                'hotel-id-2021-fgvc8']:
        identifier, target = {
            'h-and-m-personalized-fashion-recommendations': ('customer_id', 'prediction'),
            'whale-categorization-playground': ('Image', 'Id'),
            'hotel-id-2021-fgvc8': ('image', 'hotel_id'),
        }[name]
        return pair({identifier: ['a', 'b'], target: ['1', '2']})
    if name in ['freesound-audio-tagging-2019', 'google-quest-challenge',
                'jigsaw-toxic-comment-classification-challenge', 'ranzcr-clip-catheter-line-classification',
                'plant-pathology-2020-fgvc7', 'playground-series-s3e18']:
        identifier, classes = {
            'freesound-audio-tagging-2019': ('fname', getattr(module, 'CLASS_NAMES', [])),
            'google-quest-challenge': ('qa_id', getattr(module, 'CLASSES', [])),
            'jigsaw-toxic-comment-classification-challenge': ('id', getattr(module, 'CLASSES', [])),
            'ranzcr-clip-catheter-line-classification': ('StudyInstanceUID', getattr(module, 'CLASSES', [])),
            'plant-pathology-2020-fgvc7': ('image_id', ['healthy', 'multiple_diseases', 'rust', 'scab']),
            'playground-series-s3e18': ('id', ['EC1', 'EC2']),
        }[name]
        data = {identifier: [str(i) for i in range(8)]}
        for column in classes:
            data[column] = list(np.linspace(.1, .8, 8)) if name == 'google-quest-challenge' else [0, 1] * 4
        return pair(data)
    if name == 'hms-harmful-brain-activity-classification':
        return onehot(module.ID_COL, module.TARGET_COLS)
    if name == 'jigsaw-unintended-bias-in-toxicity-classification':
        columns = inspect.signature(module.grade).parameters['identity_columns'].default
        data = dict(id=[str(i) for i in range(8)], toxicity=[0, 1] * 4)
        data.update({column: [0, 0, 1, 1] * 2 for column in columns})
        return pair(data)
    numeric = {
        'denoising-dirty-documents': ('id', 'value', 0.),
        'new-york-city-taxi-fare-prediction': ('key', 'fare_amount', 0.),
        'petfinder-pawpularity-score': ('Id', 'Pawpularity', 0.),
        'predict-volcanic-eruptions-ingv-oe': ('segment_id', 'time_to_eruption', 0.),
        'us-patent-phrase-to-phrase-matching': ('id', 'score', 1.),
    }
    if name in numeric:
        identifier, target, expected = numeric[name]
        return pair({identifier: ['a', 'b', 'c'], target: [1., 2., 3.]}, expected)
    if name == 'nomad2018-predict-transparent-conductors':
        return pair(dict(formation_energy_ev_natom=[1., 2.], bandgap_energy_ev=[2., 3.]), 0.)
    if name == 'champs-scalar-coupling':
        sub, ans, _ = pair(dict(id=['a', 'b'], type=['1JHC', '2JHC'], scalar_coupling_constant=[1., 2.]))
        sub['scalar_coupling_constant'] += 1
        return sub[['id', 'scalar_coupling_constant']], ans, 0.
    if name == 'ventilator-pressure-prediction':
        sub, ans, _ = pair(dict(id=['a', 'b', 'c'], pressure=[1., 2., 3.], u_out=[0, 1, 0]))
        return sub[['id', 'pressure']], ans, 0.
    if name == 'stanford-covid-vaccine':
        sub, ans, _ = pair({'id_seqpos': ['a_0', 'a_1'], 'reactivity': [1., 2.],
                            'deg_Mg_pH10': [2., 3.], 'deg_Mg_50C': [3., 4.], 'keep': [True, True]})
        sub = sub.drop(columns='keep').assign(deg_pH10=0., deg_50C=0.)
        return sub, ans, 0.
    if name == 'osic-pulmonary-fibrosis-progression':
        ans = pd.DataFrame(dict(Patient_Week=['p1_1', 'p1_2'], FVC=[2000., 2100.], Patient=['p1', 'p1']))
        sub = ans.drop(columns='Patient').assign(Confidence=100.)
        return sub, ans, None
    if name == 'icecube-neutrinos-in-deep-ice':
        return pair(dict(event_id=[1, 2], azimuth=[0., 0.], zenith=[0., 0.]), 0.)
    if name == 'smartphone-decimeter-2022':
        return pair(dict(tripId=['a', 'a'], UnixTimeMillis=[1, 2],
                         LatitudeDegrees=[30., 31.], LongitudeDegrees=[120., 121.]), 0.)
    if name == 'rsna-breast-cancer-detection':
        return pair(dict(prediction_id=['a', 'b'], cancer=[0, 1]))
    if name == 'rsna-2022-cervical-spine-fracture-detection':
        data = {'row_id': [], 'fractured': []}
        for patient in ['a', 'b']:
            for column in module.column_order:
                data['row_id'].append(f'{patient}_{column}')
                data['fractured'].append(int(patient == 'b'))
        return pair(data, 0.)
    if name == 'hubmap-kidney-segmentation':
        ans = pd.DataFrame(dict(id=['a'], encoding=['1 4'], width_pixels=[4], height_pixels=[4]))
        return pd.DataFrame(dict(id=['a'], predicted=['1 4'])), ans, 1.
    if name == 'google-research-identify-contrails-reduce-global-warming':
        sub, ans, _ = pair(dict(record_id=['a'], encoded_pixels=['1 4'], height=[4], width=[4]))
        return sub[['record_id', 'encoded_pixels']], ans, 1.
    if name == 'tgs-salt-identification-challenge':
        return pair(dict(id=['a'], rle_mask=['1 4']))
    if name == 'vesuvius-challenge-ink-detection':
        sub, ans, _ = pair(dict(Id=['a'], Predicted=['1 4'], width=[4], height=[4]))
        return sub[['Id', 'Predicted']], ans, 1.
    if name == 'uw-madison-gi-tract-image-segmentation':
        sub, ans, _ = pair(dict(id=['case1_day1_slice_0001'], **{'class': ['large_bowel']},
                                predicted=['1 4'], image_height=[4], image_width=[4]))
        return sub[['id', 'class', 'predicted']], ans, 1.
    if name == 'kuzushiji-recognition':
        return (pd.DataFrame(dict(image_id=['a'], labels=['U+003F 3 3'])),
                pd.DataFrame(dict(image_id=['a'], labels=['U+003F 1 1 10 10'])), 1.)
    if name == '3d-object-detection-for-autonomous-vehicles':
        return pair(dict(Id=['a'], PredictionString=['1.0 0 0 0 2 4 2 0 car']))
    if name == 'siim-covid19-detection':
        return pair(dict(id=['a', 'b'], PredictionString=['opacity 1 0 0 10 10', 'none 1 0 0 1 1']))
    if name == 'vinbigdata-chest-xray-abnormalities-detection':
        ans = pd.DataFrame(dict(image_id=['a'], class_id=[0], x_min=[0.], y_min=[0.], x_max=[10.], y_max=[10.]))
        return pd.DataFrame(dict(image_id=['a'], PredictionString=['0 1 0 0 10 10'])), ans, 1.
    if name == 'tensorflow2-question-answering':
        answers = [dict(example_id='a', annotations=[dict(long_answer=dict(start_token=0, end_token=10),
                    short_answers=[dict(start_token=2, end_token=3)], yes_no_answer='NONE')])]
        sub = pd.DataFrame(dict(example_id=['a_long', 'a_short'], PredictionString=['0:10', '2:3']))
        return sub, answers, 1.
    raise NotImplementedError(f'No synthetic input adapter for {name}')
