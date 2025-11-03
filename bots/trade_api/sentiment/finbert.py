def analyze(texts: list[str]) -> dict:
    try:
        from transformers import AutoTokenizer, AutoModelForSequenceClassification, TextClassificationPipeline
        import torch
        model_name = "ProsusAI/finbert"
        tok = AutoTokenizer.from_pretrained(model_name)
        mdl = AutoModelForSequenceClassification.from_pretrained(model_name)
        pipe = TextClassificationPipeline(model=mdl, tokenizer=tok, return_all_scores=True, device=-1)
        scores = []
        for t in texts[:20]:
            res = pipe(t)[0]
            # labels: positive/negative/neutral
            d = {x['label'].lower(): x['score'] for x in res}
            scores.append(d)
        # average
        if not scores:
            return {"positive":0.33,"neutral":0.34,"negative":0.33}
        avg = {k: sum(s.get(k,0) for s in scores)/len(scores) for k in scores[0].keys()}
        return avg
    except Exception:
        return {"positive":0.33,"neutral":0.34,"negative":0.33}
