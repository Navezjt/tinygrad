from collections import OrderedDict
import unicodedata

def _get_best_indices(logits, n_best_size):
  index_and_score = sorted(enumerate(logits), key=lambda x: x[1], reverse=True)
  return list(map(lambda x: x[0], index_and_score))[:n_best_size]

def _is_punctuation(char):
  if (cp := ord(char)) in range(33, 48) or cp in range(58, 65) or cp in range(91, 97) or cp in range(123, 127):
    return True
  return unicodedata.category(char).startswith("P")

def _is_whitespace(char):
  if char == " " or char == "\t" or char == "\n" or char == "\r":
    return True
  return unicodedata.category(char) == "Zs"

def _is_control(char):
  if char == "\t" or char == "\n" or char == "\r":
    return False
  return unicodedata.category(char).startswith("C")

def _run_split_on_punc(text):
  if text in ("[UNK]", "[SEP]", "[PAD]", "[CLS]", "[MASK]"):
    return [text]
  start_new_word = True
  output = []
  for i in range(len(text)):
    if _is_punctuation(char := text[i]):
      output.append([char])
      start_new_word = True
    else:
      if start_new_word:
        output.append([])
      start_new_word = False
      output[-1].append(char)
  return ["".join(x) for x in output]

def _run_strip_accents(text):
  output = []
  for char in unicodedata.normalize("NFD", text):
    if unicodedata.category(char) != "Mn":
      output.append(char)
  return "".join(output)

def _clean_text(text):
  output = []
  for char in text:
    if not ((cp := ord(char)) == 0 or cp == 0xfffd or _is_control(char)):
      output.append(" " if _is_whitespace(char) else char)
  return "".join(output)

def _get_final_text(pred_text, orig_text):
  def _strip_spaces(text):
    ns_text = ""
    ns_to_s_map = OrderedDict()
    for i, c in enumerate(text):
      if c == " ":
        continue
      ns_to_s_map[len(ns_text)] = i
      ns_text += c
    return ns_text, ns_to_s_map

  orig_tokens = _clean_text(orig_text).strip().split()
  split_tokens = []
  for token in orig_tokens:
    if token not in ("[UNK]", "[SEP]", "[PAD]", "[CLS]", "[MASK]"):
      token = token.lower()
      token = _run_strip_accents(token)
    split_tokens.extend(_run_split_on_punc(token))

  tok_text = " ".join(" ".join(split_tokens).strip().split())
  start_position = tok_text.find(pred_text)
  if start_position == -1:
    return orig_text
  end_position = start_position + len(pred_text) - 1

  orig_ns_text, orig_ns_to_s_map = _strip_spaces(orig_text)
  tok_ns_text, tok_ns_to_s_map = _strip_spaces(tok_text)
  if len(orig_ns_text) != len(tok_ns_text):
    return orig_text
  tok_s_to_ns_map = {v: k for k, v in tok_ns_to_s_map.items()}

  orig_start_position = None
  if start_position in tok_s_to_ns_map:
    if (ns_start_position := tok_s_to_ns_map[start_position]) in orig_ns_to_s_map:
      orig_start_position = orig_ns_to_s_map[ns_start_position]
  if orig_start_position is None:
    return orig_text

  orig_end_position = None
  if end_position in tok_s_to_ns_map:
    if (ns_end_position := tok_s_to_ns_map[end_position]) in orig_ns_to_s_map:
      orig_end_position = orig_ns_to_s_map[ns_end_position]
  if orig_end_position is None:
    return orig_text

  output_text = orig_text[orig_start_position:(orig_end_position + 1)]
  return output_text

def get_bert_qa_prediction(features, example, start_end_logits):
  prelim_predictions = []
  for i, feature in enumerate(features):
    for start_index in _get_best_indices(start_end_logits[i][0], 20):
      for end_index in _get_best_indices(start_end_logits[i][1], 20):
        if start_index >= len(feature["tokens"]) or end_index >= len(feature["tokens"]):
          continue
        if start_index not in feature["token_to_orig_map"] or end_index not in feature["token_to_orig_map"]:
          continue
        if not feature["token_is_max_context"].get(start_index, False):
          continue
        if end_index < start_index or end_index - start_index + 1 > 30:
          continue

        prelim_predictions.append({
          "feature_index": i,
          "start_index": start_index,
          "end_index": end_index,
          "start_logit": start_end_logits[i][0, start_index],
          "end_logit": start_end_logits[i][1, end_index]
        })
  predictions = sorted(prelim_predictions, key=lambda x: (x["start_logit"] + x["end_logit"]), reverse=True)

  if len(predictions) > 0:
    feature = features[predictions[0]["feature_index"]]
    tok_tokens = feature["tokens"][predictions[0]["start_index"]:(predictions[0]["end_index"] + 1)]
    orig_doc_start = feature["token_to_orig_map"][predictions[0]["start_index"]]
    orig_doc_end = feature["token_to_orig_map"][predictions[0]["end_index"]]
    orig_tokens = example["context"][orig_doc_start:(orig_doc_end + 1)]
    tok_text = " ".join(tok_tokens).replace(" ##", "").replace("##", "")
    tok_text = " ".join(tok_text.strip().split())
    orig_text = " ".join(orig_tokens)
    return _get_final_text(tok_text, orig_text)
  return "empty"
