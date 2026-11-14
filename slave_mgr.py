# -*- coding: utf-8 -*-
"""Slave Channel Manager.

CP1~CP4의 경계조건 자료를 독립적으로 준비하고 CATHENA 실행 작업을 만듭니다.
빈 줄, 헤더, 숫자가 아닌 행은 입력 자료에서 자동으로 건너뜁니다.

실행:
    python slave_mgr.py
    python slave_mgr.py channel_set
    python slave_mgr.py channel_1.inp
"""

from pathlib import Path
import re
import shutil
import subprocess
import sys


# ============================================================
# 설정
# ============================================================

CP_FILES = {
    "1": "hd23bc.out",
    "2": "hd41bc.out",
    "3": "hd67bc.out",
    "4": "hc85bc.out",
}

BC_FALLBACK_FILES = {
    "1": ("out85.dat", "out86.dat"),
    "2": ("out87.dat", "out88.dat"),
    "3": ("out89.dat", "out90.dat"),
    "4": ("out91.dat", "out92.dat"),
}

PWRTAB_HEADER_LINES = [
    "'INPUT TABLE', 'PWRTAB'/",
    "1, 972, 'power.inp',.FALSE.,0 / from power.dat",
]

BC_HEADERS = [
    "TIME", "IHPRES", "IHHG", "IHHF", "IHALFA",
    "OHPRES", "OHHG", "OHHF", "OHALFA",
]
POWER_HEADERS = ["TIME", "PWR_SING"]

NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?"
    r"(?:\d+(?:\.\d*)?|\.\d+)"
    r"(?:[EeDd][-+]?\d)?"
)


# ============================================================
# 일반 유틸리티
# ============================================================

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def find_file_case_insensitive(folder: Path, filename: str) -> Path | None:
    """대소문자와 무관하게 파일을 찾는다."""
    if not folder.is_dir():
        return None

    target = filename.lower()
    for item in folder.iterdir():
        if item.is_file() and item.name.lower() == target:
            return item
    return None


def copy_file_if_different(source: Path, destination: Path) -> None:
    """원본과 대상이 다를 때만 파일을 복사한다."""
    if not source.is_file():
        return
    if source.resolve() == destination.resolve():
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def allowed_support_file(path: Path) -> bool:
    """채널 입력과 별도로 복사할 보조 파일인지 확인한다."""
    return (
        path.name.lower() in {"common_input.txt", "pwrtab.pwr"}
        or path.suffix.lower() == ".dat"
    )


def copy_support_files(source_dir: Path, target_dir: Path) -> None:
    """DAT, common_input.txt, PWRTAB.PWR만 복사한다."""
    if not source_dir.is_dir():
        return

    for source in source_dir.iterdir():
        if source.is_file() and allowed_support_file(source):
            copy_file_if_different(source, target_dir / source.name)


# ============================================================
# 숫자 자료 처리
# ============================================================

def is_numeric_data_line(
    line: str, expected_columns: int, allow_extra_columns: bool = False
) -> bool:
    fields = line.split()
    if len(fields) < expected_columns:
        return False
    if not allow_extra_columns and len(fields) != expected_columns:
        return False

    try:
        for field in fields[:expected_columns]:
            float(field.replace("D", "E").replace("d", "e"))
    except ValueError:
        return False
    return True


def read_numeric_rows(
    source_file: Path, expected_columns: int, allow_extra_columns: bool = False
) -> list[str]:
    """빈 줄·헤더·문자 행을 제외한 숫자 행만 읽는다."""
    if not source_file.is_file():
        raise FileNotFoundError(f"원본 파일이 없습니다: {source_file}")

    rows: list[str] = []
    for line in read_text(source_file).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if is_numeric_data_line(stripped, expected_columns, allow_extra_columns):
            rows.append(" ".join(stripped.split()[:expected_columns]))

    if not rows:
        rule = f"최소 {expected_columns}개" if allow_extra_columns else f"정확히 {expected_columns}개"
        raise ValueError(
            f"{source_file.name}: 유효한 숫자 데이터가 없습니다 "
            f"(필요한 열 수: {rule})."
        )
    return rows


def read_numeric_values(
    source_file: Path, expected_columns: int, allow_extra_columns: bool = False
) -> list[list[float]]:
    return [
        [float(value.replace("D", "E").replace("d", "e")) for value in row.split()]
        for row in read_numeric_rows(source_file, expected_columns, allow_extra_columns)
    ]


# ============================================================
# 경계조건 표
# ============================================================

def create_table_input(
    source_file: Path, output_file: Path, headers: list[str], expected_columns: int
) -> int:
    rows = read_numeric_rows(source_file, expected_columns)
    header = ", ".join(f"'{name}'" for name in headers) + "/"
    output_file.write_text(
        "\n".join([header, *(f"{row}/" for row in rows)]) + "\n",
        encoding="utf-8",
    )
    return len(rows)


def create_fallback_bc_table(cp: str, source_dir: Path, output_file: Path) -> int:
    """짝을 이룬 out*.dat 두 개를 TIME으로 맞춰 경계조건 표를 만든다."""
    first_name, second_name = BC_FALLBACK_FILES[cp]
    first_file = source_dir / first_name
    second_file = source_dir / second_name
    missing = [p.name for p in (first_file, second_file) if not p.is_file()]
    if missing:
        raise FileNotFoundError(f"CP{cp}: 대체 파일이 없습니다: {', '.join(missing)}")

    first_rows = read_numeric_values(first_file, 5, allow_extra_columns=True)
    second_rows = read_numeric_values(second_file, 5, allow_extra_columns=True)
    second_by_time: dict[float, list[list[float]]] = {}
    for row in second_rows:
        second_by_time.setdefault(row[0], []).append(row)

    combined_rows: list[str] = []
    unmatched = 0
    for first_row in first_rows:
        time_value = first_row[0]
        tolerance = max(1.0e-12, abs(time_value) * 1.0e-9)
        matched_time = next(
            (
                candidate for candidate, rows in second_by_time.items()
                if rows and abs(candidate - time_value) <= tolerance
            ),
            None,
        )
        if matched_time is None:
            unmatched += 1
            continue
        second_row = second_by_time[matched_time].pop(0)
        values = first_row + second_row[1:5]
        combined_rows.append(" ".join(f"{value:.12g}" for value in values))

    if unmatched:
        print(f"  경고 CP{cp}: TIME이 맞지 않는 첫 번째 파일 행 {unmatched}개를 제외했습니다.")
    remaining = sum(len(rows) for rows in second_by_time.values())
    if remaining:
        print(f"  경고 CP{cp}: TIME이 맞지 않는 두 번째 파일 행 {remaining}개를 제외했습니다.")
    if not combined_rows:
        raise ValueError(f"CP{cp}: 두 대체 파일 사이에 일치하는 TIME 숫자 데이터가 없습니다.")

    header = ", ".join(f"'{name}'" for name in BC_HEADERS) + "/"
    output_file.write_text(
        "\n".join([header, *(f"{row}/" for row in combined_rows)]) + "\n",
        encoding="utf-8",
    )
    return len(combined_rows)


def validate_cp_source(cp: str, source_dir: Path) -> bool:
    """한 CP의 자료만 독립적으로 검사한다."""
    primary = source_dir / CP_FILES[cp]
    if primary.is_file():
        read_numeric_rows(primary, 9)
        return True

    first_name, second_name = BC_FALLBACK_FILES[cp]
    first_file = source_dir / first_name
    second_file = source_dir / second_name
    first_rows = read_numeric_values(first_file, 5, allow_extra_columns=True)
    second_rows = read_numeric_values(second_file, 5, allow_extra_columns=True)
    for first_row in first_rows:
        tolerance = max(1.0e-12, abs(first_row[0]) * 1.0e-9)
        if any(abs(second_row[0] - first_row[0]) <= tolerance for second_row in second_rows):
            return True
    raise ValueError(f"{first_name}와 {second_name} 사이에 일치하는 TIME 숫자 데이터가 없습니다.")


def create_cp_bc_table(cp: str, source_dir: Path, output_file: Path) -> tuple[int, str]:
    primary = source_dir / CP_FILES[cp]
    if primary.is_file():
        return create_table_input(primary, output_file, BC_HEADERS, 9), primary.name

    count = create_fallback_bc_table(cp, source_dir, output_file)
    first_name, second_name = BC_FALLBACK_FILES[cp]
    return count, f"{first_name}+{second_name}"


# ============================================================
# PWRTAB.PWR 및 bound.inp
# ============================================================

def create_pwrtab_file(template_file: Path, power_source: Path, output_file: Path) -> int:
    """power.dat를 사용해 PWRTAB.PWR을 생성 또는 갱신한다."""
    if template_file.is_file():
        template_lines = read_text(template_file).splitlines()
        if len(template_lines) < 2:
            raise ValueError("PWRTAB.PWR에는 최소 두 줄의 헤더가 필요합니다.")
        output_lines = template_lines[:2]
    else:
        output_lines = PWRTAB_HEADER_LINES.copy()

    power_rows = read_numeric_rows(power_source, expected_columns=2)
    matches = list(NUMBER_PATTERN.finditer(output_lines[1].split("/", 1)[0]))
    if len(matches) < 2:
        raise ValueError("PWRTAB.PWR 두 번째 줄에서 두 번째 숫자 항목을 찾을 수 없습니다.")
    second = matches[1]
    output_lines[1] = output_lines[1][:second.start()] + str(len(power_rows)) + output_lines[1][second.end():]
    power_header = ", ".join(f"'{name}'" for name in POWER_HEADERS) + "/"
    output_lines.extend(["", power_header, *(f"{row}/" for row in power_rows)])
    output_file.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    return len(power_rows)


def update_bound_file(file_path: Path, new_value: int, cp: str, source_label: str, line_number: int = 2) -> None:
    if not file_path.is_file():
        raise FileNotFoundError(f"필수 파일이 없습니다: {file_path.name}")
    lines = read_text(file_path).splitlines(keepends=True)
    if len(lines) < line_number:
        raise ValueError(f"{file_path.name}: {line_number}번째 줄이 없습니다.")

    code_part, slash, comment_part = lines[line_number - 1].partition("/")
    matches = list(NUMBER_PATTERN.finditer(code_part))
    if len(matches) < 2:
        raise ValueError(f"{file_path.name} {line_number}번째 줄에서 두 번째 숫자 항목을 찾을 수 없습니다.")
    second = matches[1]
    code_part = code_part[:second.start()] + str(new_value) + code_part[second.end():]

    if slash:
        comment_part = re.sub(
            r"(?:hd\d+bc\.out|hc\d+bc\.out|out\d+\.dat(?:\+out\d+\.dat)?)",
            source_label, comment_part, flags=re.IGNORECASE,
        )
        comment_part = re.sub(r"\(CP[1-4]\)", f"(CP{cp})", comment_part, flags=re.IGNORECASE)
        if not re.search(r"\(CP[1-4]\)", comment_part, flags=re.IGNORECASE):
            comment_part = comment_part.rstrip() + f" (CP{cp})"
        lines[line_number - 1] = code_part + slash + comment_part
    else:
        lines[line_number - 1] = code_part
    file_path.write_text("".join(lines), encoding="utf-8")


# ============================================================
# 선택 및 폴더 처리
# ============================================================

def select_cp() -> list[str]:
    while True:
        answer = input("실행할 CP 번호를 입력하세요 (예: 1 / 1,3 / 2,3,4 / ALL): ").strip().upper()
        if answer in {"ALL", "A"}:
            return ["1", "2", "3", "4"]
        normalized = re.sub(r"CP\s*([1-4])", r"\1", answer)
        parts = [part for part in re.split(r"[,;+\s]+", normalized) if part]
        if parts and all(part in CP_FILES for part in parts):
            return list(dict.fromkeys(parts))
        print("잘못된 입력입니다. CP1~CP4 중에서 선택하세요.")


def select_batch_file(working_dir: Path) -> str:
    """배치 파일을 한 번만 선택한다. 기본값은 runcat3561_ps1.bat이다."""
    default = "runcat3561_ps1.bat"
    alternative = "runcat3561.bat"
    while True:
        answer = input(
            f"실행 배치 파일 [{default}] (엔터=기본값, 2={alternative}): "
        ).strip()
        if not answer or answer == "1":
            selected = default
        elif answer == "2" or answer.lower() == alternative.lower():
            selected = alternative
        elif answer.lower() == default.lower():
            selected = default
        else:
            print("잘못된 선택입니다. 엔터, 1 또는 2를 입력하세요.")
            continue

        if find_file_case_insensitive(working_dir, selected):
            return selected
        print(f"선택한 배치 파일이 현재 폴더에 없습니다: {selected}")


def ask_extra_run_arguments() -> str:
    return input("runcat에 전달할 추가 실행 인수 (없으면 Enter): ").strip()


def ask_channel_set_name() -> str:
    while True:
        name = input("채널 입력명(fn)을 입력하세요: ").strip()
        if not name:
            print("입력명이 비어 있습니다.")
            continue
        if Path(name).name != name:
            print("파일명 또는 폴더명만 입력하세요.")
            continue
        return name[:-4] if name.lower().endswith(".inp") else name


def get_channel_set_name() -> str:
    """첫 번째 명령줄 인수가 있으면 질문 없이 채널 입력명으로 사용한다."""
    if len(sys.argv) < 2:
        return ask_channel_set_name()

    name = sys.argv[1].strip()
    if not name:
        raise ValueError("첫 번째 실행 인수의 채널 입력명이 비어 있습니다.")
    if Path(name).name != name:
        raise ValueError("첫 번째 실행 인수에는 파일명 또는 폴더명만 지정할 수 있습니다.")

    if name.lower().endswith(".inp"):
        name = name[:-4]

    print(f"명령줄 인수의 채널 입력명을 사용합니다: {name}")
    return name


def remove_existing_cp_folders(working_dir: Path) -> bool:
    existing = [working_dir / f"CP{i}" for i in range(1, 5) if (working_dir / f"CP{i}").is_dir()]
    if not existing:
        return True
    print("\n기존 CP 폴더가 발견되었습니다.")
    for folder in existing:
        print(f"  {folder.name}")
    answer = input("기존 CP 폴더를 모두 삭제할까요? (Y/N): ").strip().upper()
    if answer not in {"Y", "YES"}:
        print("작업을 중단합니다.")
        return False
    for folder in existing:
        shutil.rmtree(folder)
    return True


def create_cp_folders(working_dir: Path, cp_numbers: list[str]) -> dict[str, Path]:
    """여러 CP가 유효할 때만 CP별 폴더를 만든다."""
    bound_source = find_file_case_insensitive(working_dir, "bound.inp")
    targets: dict[str, Path] = {}
    for cp in cp_numbers:
        target = working_dir / f"CP{cp}"
        target.mkdir()
        copy_support_files(working_dir, target)
        if bound_source:
            copy_file_if_different(bound_source, target / "bound.inp")
        targets[cp] = target
    return targets


# ============================================================
# Channel 입력 해석 및 복사
# ============================================================

def find_channel_input_files(channel_dir: Path) -> list[Path]:
    if not channel_dir.is_dir():
        return []
    preferred = find_file_case_insensitive(channel_dir, f"{channel_dir.name}.inp")
    if preferred:
        return [preferred]
    return sorted(
        [p for p in channel_dir.iterdir() if p.is_file() and p.suffix.lower() == ".inp"],
        key=lambda p: p.name.lower(),
    )


def resolve_channel_sources(working_dir: Path, fn: str) -> list[tuple[Path, list[Path]]]:
    """직접 INP, Channel 루트, Channel 하위 폴더의 INP를 찾는다."""
    direct_input = find_file_case_insensitive(working_dir, f"{fn}.inp")
    if direct_input:
        return [(direct_input.parent, [direct_input])]

    channel_root = working_dir / fn
    if not channel_root.is_dir():
        raise FileNotFoundError(f"{fn}.inp 또는 {fn} 폴더를 찾을 수 없습니다.")
    root_inputs = find_channel_input_files(channel_root)
    if root_inputs:
        return [(channel_root, root_inputs)]
    sources = [
        (folder, inputs)
        for folder in sorted((p for p in channel_root.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
        if (inputs := find_channel_input_files(folder))
    ]
    if not sources:
        raise FileNotFoundError(f"{channel_root}에서 .inp 파일을 찾을 수 없습니다.")
    return sources


def copy_channel_inputs(source_dir: Path, input_files: list[Path], target_dir: Path, working_dir: Path) -> list[Path]:
    """INP 및 짝 AUX를 대상 CP 루트에 복사하고 실제 복사한 INP를 반환한다."""
    copied_inputs: list[Path] = []
    for inp_file in input_files:
        if not inp_file.is_file() or inp_file.suffix.lower() != ".inp":
            continue
        target_input = target_dir / inp_file.name
        if target_input.exists() and inp_file.resolve() != target_input.resolve():
            if inp_file.read_bytes() != target_input.read_bytes():
                raise FileExistsError(f"서로 다른 채널 입력 파일 이름이 중복됩니다: {inp_file.name}")
        else:
            copy_file_if_different(inp_file, target_input)
        copied_inputs.append(target_input)

        aux_file = find_file_case_insensitive(source_dir, inp_file.stem + ".aux")
        if aux_file is None:
            aux_file = find_file_case_insensitive(working_dir, "base.aux")
        if aux_file is not None:
            copy_file_if_different(aux_file, target_dir / (inp_file.stem + ".aux"))
    copy_support_files(source_dir, target_dir)
    return copied_inputs


# ============================================================
# CP 준비 및 실행
# ============================================================

def prepare_one_cp(cp: str, target_dir: Path, source_dir: Path, power_source: Path | None) -> tuple[int, int | None, str]:
    bc_count, bc_source_label = create_cp_bc_table(cp, source_dir, target_dir / "rhdbcs.inp")
    if power_source is not None and power_source.is_file():
        power_count = create_pwrtab_file(target_dir / "PWRTAB.PWR", power_source, target_dir / "PWRTAB.PWR")
    elif (target_dir / "PWRTAB.PWR").is_file():
        print(f"  CP{cp}: power.dat가 없어 기존 PWRTAB.PWR을 사용합니다.")
        power_count = None
    else:
        raise FileNotFoundError("power.dat와 기존 PWRTAB.PWR이 모두 없습니다.")
    update_bound_file(target_dir / "bound.inp", bc_count, cp, bc_source_label)
    return bc_count, power_count, bc_source_label


def run_cathena_jobs(jobs: list[tuple[Path, str, str, str]], extra_args: str) -> list[dict]:
    """중복 없이 등록된 CATHENA 작업을 각각 실행하고 성공 결과만 반환한다."""
    processes: list[dict] = []
    successful_runs: list[dict] = []
    print("\nCATHENA 실행을 시작합니다.")

    for run_dir, batch_name, input_arg, description in jobs:
        batch_path = find_file_case_insensitive(run_dir, batch_name)
        if batch_path is None:
            # 단일 CP는 현재 폴더, 복수 CP는 그 하위 CP 폴더에서 실행한다.
            # 배치 파일은 두 경우 모두 작업 시작 폴더에 한 번만 둔다.
            batch_path = find_file_case_insensitive(run_dir.parent, batch_name)
        if batch_path is None:
            print(f"오류: {description}: 배치 파일이 없습니다: {batch_name}")
            continue
        command = f'call "{batch_path}" "{input_arg}"'
        if extra_args:
            command += f" {extra_args}"
        print(f"\n실행: {description}")
        print(f"  작업 폴더: {run_dir}")
        print(f"  명령: {command}")
        try:
            process = subprocess.Popen(
                ["cmd.exe", "/d", "/c", command], cwd=str(run_dir),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            processes.append({"process": process, "description": description, "run_dir": run_dir, "input_arg": input_arg})
        except OSError as error:
            print(f"오류: {description}: 실행을 시작할 수 없습니다: {error}")

    print("\nCATHENA 실행이 끝나기를 기다립니다.")
    for item in processes:
        try:
            return_code = item["process"].wait()
            if return_code == 0:
                print(f"성공: {item['description']}")
                successful_runs.append({k: item[k] for k in ("description", "run_dir", "input_arg")})
            else:
                print(f"오류: {item['description']}: runcat 반환 코드 {return_code}")
        except OSError as error:
            print(f"오류: {item['description']}: 실행 상태를 확인할 수 없습니다: {error}")
    print(f"\nCATHENA 실행 완료: 성공 {len(successful_runs)}건 / 요청 {len(jobs)}건")
    return successful_runs


def main() -> None:
    working_dir = Path.cwd()
    bc_source_dir = working_dir.parent
    print("=" * 64)
    print(" Slave 채널 관리자")
    print("=" * 64)
    print(f"현재 폴더       : {working_dir}")
    print(f"경계조건 원본   : {bc_source_dir}\n")

    if not remove_existing_cp_folders(working_dir):
        return
    selected_cps = select_cp()
    batch_name = select_batch_file(working_dir)
    channel_name = get_channel_set_name()
    channel_sources = resolve_channel_sources(working_dir, channel_name)
    power_source = bc_source_dir / "power.dat"
    if power_source.is_file():
        read_numeric_rows(power_source, 2)
    elif not (working_dir / "PWRTAB.PWR").is_file():
        raise FileNotFoundError("상위 폴더의 power.dat와 현재 폴더의 PWRTAB.PWR이 모두 없습니다.")

    print("\nCP 입력 데이터를 확인합니다.")
    valid_cps: list[str] = []
    for cp in selected_cps:
        try:
            validate_cp_source(cp, bc_source_dir)
            valid_cps.append(cp)
            print(f"  CP{cp}: 확인 완료")
        except (ValueError, FileNotFoundError, OSError) as error:
            print(f"  경고 CP{cp}: {error} -- 이 CP는 건너뜁니다.")
    if not valid_cps:
        print("\n유효한 CP가 없습니다. 작업을 종료합니다.")
        return

    # 유효 CP가 하나면 현재 폴더를 그대로 사용한다.
    target_dirs = ({valid_cps[0]: working_dir} if len(valid_cps) == 1 else create_cp_folders(working_dir, valid_cps))
    print("\nCP별 입력 파일을 준비합니다.")
    prepared_cps: list[str] = []
    for cp in valid_cps:
        target_dir = target_dirs[cp]
        try:
            bc_count, power_count, bc_source = prepare_one_cp(cp, target_dir, bc_source_dir, power_source if power_source.is_file() else None)
            prepared_cps.append(cp)
            power_status = f"power 행={power_count}" if power_count is not None else "기존 PWRTAB.PWR 사용"
            print(f"  CP{cp}: RHDBCS={bc_count}행, 원본={bc_source}, {power_status}")
        except (ValueError, FileNotFoundError, OSError) as error:
            print(f"  오류 CP{cp}: {error} -- 이 CP는 건너뜁니다.")
            if len(valid_cps) > 1 and target_dir.is_dir():
                shutil.rmtree(target_dir)
    if not prepared_cps:
        print("\n입력 준비에 성공한 CP가 없습니다.")
        return

    # 준비 실패로 하나만 남은 경우에는 CP 폴더를 만들지 않는 규칙을 유지한다.
    if len(prepared_cps) == 1 and len(valid_cps) > 1:
        cp = prepared_cps[0]
        old_target = target_dirs[cp]
        for item in old_target.iterdir():
            copy_file_if_different(item, working_dir / item.name)
        old_target.rmdir()
        target_dirs[cp] = working_dir

    print("\n채널 입력 파일을 복사합니다.")
    copied_inputs_by_cp: dict[str, list[Path]] = {}
    for cp in prepared_cps:
        target_dir = target_dirs[cp]
        copied_inputs: list[Path] = []
        try:
            for source_dir, input_files in channel_sources:
                copied_inputs.extend(copy_channel_inputs(source_dir, input_files, target_dir, working_dir))
            copied_inputs_by_cp[cp] = list(dict.fromkeys(copied_inputs))
            for input_file in copied_inputs_by_cp[cp]:
                print(f"  CP{cp}: {input_file.name} 복사 완료")
        except (FileExistsError, OSError) as error:
            print(f"  오류 CP{cp}: 채널 입력 파일 복사 실패: {error} -- 이 CP는 건너뜁니다.")

    runnable_cps = [cp for cp in prepared_cps if copied_inputs_by_cp.get(cp)]
    if not runnable_cps:
        print("\n실행할 채널 입력 파일이 없습니다.")
        return
    print("\n입력 파일 준비가 완료되었습니다.")
    extra_args = ask_extra_run_arguments()

    # 각 (CP, INP) 조합은 정확히 한 번만 작업으로 등록한다.
    jobs: list[tuple[Path, str, str, str]] = []
    seen: set[tuple[Path, str]] = set()
    for cp in runnable_cps:
        run_dir = target_dirs[cp]
        for input_file in copied_inputs_by_cp[cp]:
            key = (run_dir.resolve(), input_file.stem.lower())
            if key in seen:
                continue
            seen.add(key)
            jobs.append((run_dir, batch_name, input_file.stem, f"CP{cp} / {input_file.name}"))
    successful_runs = run_cathena_jobs(jobs, extra_args)
    if successful_runs:
        print("\n성공한 실행 결과:")
        for result in successful_runs:
            print(f"  {result['description']} : {result['run_dir']}")
        print("결과 파일 정리는 성공한 실행만 대상으로 후속 처리할 수 있습니다.")
    else:
        print("\n성공한 실행이 없어 정리할 결과가 없습니다.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n사용자 요청으로 작업을 중단했습니다.")
        sys.exit(1)
    except Exception as error:
        print(f"\n오류: {error}")
        sys.exit(1)
