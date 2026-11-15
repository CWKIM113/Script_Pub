def update_bound_file(
    file_path: Path, header_count: int, row_count: int, headers: list[str], cp: str, source_label: str
) -> None:
    """bound.inp가 없으면 지정된 규칙으로 신규 생성하고, 있으면 두 번째 줄의 정수 2개를 자동 갱신한다."""
    # TIME을 제외한 첫 번째 헤더 이름 추출 (기본값 IHPRES)
    non_time_headers = [h for h in headers if h.upper() != "TIME"]
    first_sub_header = non_time_headers[0] if non_time_headers else "IHPRES"

    if not file_path.is_file():
        # bound.inp 신규 생성 규칙 (불필요한 0.0, .TRUE./ 및 빈 줄 제거)
        default_content = (
            "'INPUT TABLE','RHDBCS',.TRUE./\n"
            f"{header_count}, {row_count}, 'rhdbcs.inp',.FALSE.,0 / from {source_label} (CP{cp})\n"
            "\n"
            "'TIME VAR.','RIHPVST',.TRUE./\n"
            f"'RHDBCS', '{first_sub_header}'/\n"
            "'INLETHDR', 'PRESS',.FALSE./\n"
            "/\n"
        )
        file_path.write_text(default_content, encoding="utf-8")
        return

    lines = read_text(file_path).splitlines(keepends=True)
    if len(lines) < 2:
        raise ValueError(f"{file_path.name}: 최소 두 줄 이상이어야 합니다.")

    # 2번째 줄(인덱스 1)의 첫 정수(헤더 수), 둘째 정수(행 수) 갱신
    code_part, slash, comment_part = lines[1].partition("/")
    matches = list(NUMBER_PATTERN.finditer(code_part))
    
    if len(matches) >= 2:
        first_num = matches[0]
        second_num = matches[1]
        code_part = (
            code_part[:first_num.start()]
            + str(header_count)
            + code_part[first_num.end():second_num.start()]
            + str(row_count)
            + code_part[second_num.end():]
        )
    else:
        code_part = f"{header_count}, {row_count}, 'rhdbcs.inp',.FALSE.,0 "

    if slash:
        comment_part = re.sub(
            r"(?:hd\d+bc\.out|hc\d+bc\.out|out\d+\.dat(?:\+out\d+\.dat)?)",
            source_label, comment_part, flags=re.IGNORECASE,
        )
        comment_part = re.sub(r"\(CP[1-4]\)", f"(CP{cp})", comment_part, flags=re.IGNORECASE)
        if not re.search(r"\(CP[1-4]\)", comment_part, flags=re.IGNORECASE):
            comment_part = comment_part.rstrip() + f" (CP{cp})"
        lines[1] = code_part + slash + comment_part
    else:
        lines[1] = code_part + f"/ from {source_label} (CP{cp})\n"

    file_path.write_text("".join(lines), encoding="utf-8")