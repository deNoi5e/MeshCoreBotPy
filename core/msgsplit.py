def str_byte_len(text: str) -> int:
    return len(text.encode('utf-8'))


def split_msg(msg: str, sender: str, max_len: int) -> list[str]:
    result = []
    words = msg.split(" ")
    word_index = 0
    while word_index < len(words):
        if not sender == "":
            part = f"@[{sender}] {words[word_index]}"
        else:
            part = f"{words[word_index]}"

        word_index += 1
        while word_index < len(words) and str_byte_len(part + f" {words[word_index]}") <= max_len:
            part += f" {words[word_index]}"
            word_index += 1
        result.append(part)
    return result
