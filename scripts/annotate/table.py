import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
for k, v in d['faces'].items():
    print(k, v['family'], v['mfcad_class'], v['feature'])
print('---')
for f in d['features']:
    print(f['id'], f['family'], f['mfcad_class'], f['faces'], f['confidence'], f['params'])
