
# -*- coding: utf-8 -*-
"""
Slave Channel Manager

- CP1~CP4 boundary data processing
- Primary hd??bc.out: exactly 9 numeric columns
- Fallback out85.dat~out92.dat: first 5 numeric columns
- Blank lines, headers and malformed rows are skipped
- Fallback files are paired by TIME
- Create rhdbcs.inp
- Create/update PWRTAB.PWR from parent power.dat
- Update line 2 of bound.inp
- Select any combination of CP1~CP4
- Create CP folders only when 2 or more CPs are valid
- Copy only required input files
- Channel INP and matching AUX files are placed in CP root
- common_input.txt is copied without parsing

Run:
    python slave_mgr.py
"""

from pathlib import Path
import re
import shutil
import sys


# ============================================================
# Configuration
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
    r"(?:[EeDd][-+]?\d+)?"
)


# ============================================================
# General utilities
# ============================================================

def read_text(path):
    return path.read_text(encoding="utf-8", errors="replace")


def find_file_case_insensitive(folder, filename):
    """Find a file without depending on filename case."""
    if not folder.is_dir():
        return None

    target = filename.lower()

    for item in folder.iterdir():
        if item.is_file() and item.name.lower() == target:
            return item

    return None


def copy_file_if_different(source, destination):
    """Copy a file unless source and destination are the same file."""
    if not source.is_file():
        return

    if source.resolve() == destination.resolve():
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def allowed_support_file(path):
    """
    Only these non-channel files are allowed:
      *.dat
      common_input.txt
      PWRTAB.PWR

    INP and AUX files are handled separately.
    """
    name = path.name.lower()

    if name == "common_input.txt":
        return True

    if name == "pwrtab.pwr":
        return True

    if path.suffix.lower() == ".dat":
        return True

    return False


def copy_support_files(source_dir, target_dir):
    """Copy only DAT, common_input.txt and PWRTAB.PWR."""
    if not source_dir.is_dir():
        return

    for source in source_dir.iterdir():
        if not source.is_file():
            continue

        if not allowed_support_file(source):
            continue

        copy_file_if_different(
            source,
            target_dir / source.name
        )


# ============================================================
# Numeric data processing
# ============================================================

def is_numeric_data_line(
    line,
    expected_columns,
    allow_extra_columns=False,
):
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
    source_file,
    expected_columns,
    allow_extra_columns=False,
):
    """Read numeric rows, skipping blank and invalid lines."""
    if not source_file.is_file():
        raise FileNotFoundError(
            f"Source file not found: {source_file}"
        )

    rows = []

    for line in read_text(source_file).splitlines():
        stripped = line.strip()

        if not stripped:
            continue

        if is_numeric_data_line(
            stripped,
            expected_columns,
            allow_extra_columns,
        ):
            rows.append(
                " ".join(stripped.split()[:expected_columns])
            )

    if not rows:
        rule = (
            f"at least {expected_columns}"
            if allow_extra_columns
            else str(expected_columns)
        )

        raise ValueError(
            f"{source_file.name}: 유효한 숫자 데이터가 없습니다. "
            f"(필요한 열 수: {rule})"
        )

    return rows


def read_numeric_values(
    source_file,
    expected_columns,
    allow_extra_columns=False,
):
    rows = read_numeric_rows(
        source_file,
        expected_columns,
        allow_extra_columns,
    )

    return [
        [
            float(value.replace("D", "E").replace("d", "e"))
            for value in row.split()
        ]
        for row in rows
    ]


# ============================================================
# Boundary condition table
# ============================================================

def create_table_input(
    source_file,
    output_file,
    headers,
    expected_columns,
):
    rows = read_numeric_rows(
        source_file,
        expected_columns,
    )

    header = ", ".join(
        f"'{name}'" for name in headers
    ) + "/"

    output_lines = [header]
    output_lines.extend(
        f"{row}/" for row in rows
    )

    output_file.write_text(
        "\n".join(output_lines) + "\n",
        encoding="utf-8",
    )

    return len(rows)


def create_fallback_bc_table(cp, source_dir, output_file):
    """
    Combine paired out*.dat files.

    Only the first five numeric columns are used.
    Extra columns are ignored.
    Rows are matched by TIME.
    """

    first_name, second_name = BC_FALLBACK_FILES[cp]

    first_file = source_dir / first_name
    second_file = source_dir / second_name

    missing = [
        p.name
        for p in (first_file, second_file)
        if not p.is_file()
    ]

    if missing:
        raise FileNotFoundError(
            f"CP{cp}: fallback 파일을 찾을 수 없습니다: "
            + ", ".join(missing)
        )

    first_rows = read_numeric_values(
        first_file, 5, allow_extra_columns=True
    )

    second_rows = read_numeric_values(
        second_file, 5, allow_extra_columns=True
    )

    second_by_time = {}

    for row in second_rows:
        second_by_time.setdefault(
            row[0], []
        ).append(row)

    combined_rows = []
    unmatched = 0

    for first_row in first_rows:
        time_value = first_row[0]
        tolerance = max(
            1.0e-12,
            abs(time_value) * 1.0e-9,
        )

        matched_time = None

        for candidate in second_by_time:
            if abs(candidate - time_value) <= tolerance:
                if second_by_time[candidate]:
                    matched_time = candidate
                    break

        if matched_time is None:
            unmatched += 1
            continue

        second_row = second_by_time[matched_time].pop(0)

        values = first_row + second_row[1:5]

        combined_rows.append(
            " ".join(f"{value:.12g}" for value in values)
        )

    if unmatched:
        print(
            f"WARNING CP{cp}: "
            f"{unmatched}개 TIME 데이터가 짝을 찾지 못해 제외되었습니다."
        )

    remaining = sum(
        len(rows) for rows in second_by_time.values()
    )

    if remaining:
        print(
            f"WARNING CP{cp}: "
            f"{remaining}개 데이터가 짝을 찾지 못해 제외되었습니다."
        )

    if not combined_rows:
        raise ValueError(
            f"CP{cp}: 두 fallback 파일 사이에 "
            f"일치하는 TIME 데이터가 없습니다."
        )

    header = ", ".join(
        f"'{name}'" for name in BC_HEADERS
    ) + "/"

    output_lines = [header]
    output_lines.extend(
        f"{row}/" for row in combined_rows
    )

    output_file.write_text(
        "\n".join(output_lines) + "\n",
        encoding="utf-8",
    )

    return len(combined_rows)


def validate_cp_source(cp, source_dir):
    """Validate one CP independently."""
    primary = source_dir / CP_FILES[cp]

    if primary.is_file():
        read_numeric_rows(primary, 9)
        return True

    first_name, second_name = BC_FALLBACK_FILES[cp]

    first_file = source_dir / first_name
    second_file = source_dir / second_name

    first_rows = read_numeric_values(
        first_file, 5, allow_extra_columns=True
    )

    second_rows = read_numeric_values(
        second_file, 5, allow_extra_columns=True
    )

    for first_row in first_rows:
        tolerance = max(
            1.0e-12,
            abs(first_row[0]) * 1.0e-9,
        )

        if any(
            abs(second_row[0] - first_row[0]) <= tolerance
            for second_row in second_rows
        ):
            return True

    raise ValueError(
        f"{first_name}와 {second_name} 사이에 "
        f"일치하는 TIME 데이터가 없습니다."
    )


def create_cp_bc_table(cp, source_dir, output_file):
    primary = source_dir / CP_FILES[cp]

    if primary.is_file():
        count = create_table_input(
            primary,
            output_file,
            BC_HEADERS,
            9,
        )

        return count, primary.name

    count = create_fallback_bc_table(
        cp,
        source_dir,
        output_file,
    )

    first_name, second_name = BC_FALLBACK_FILES[cp]

    return count, f"{first_name}+{second_name}"


# ============================================================
# PWRTAB.PWR
# ============================================================

def create_pwrtab_file(
    template_file,
    power_source,
    output_file,
):
    """Create PWRTAB.PWR using power.dat."""
    if template_file.is_file():
        template_lines = read_text(template_file).splitlines()

        if len(template_lines) < 2:
            raise ValueError(
                "PWRTAB.PWR에는 최소 두 줄의 header가 필요합니다."
            )

        output_lines = template_lines[:2]

    else:
        output_lines = PWRTAB_HEADER_LINES.copy()

    power_rows = read_numeric_rows(
        power_source,
        expected_columns=2,
    )

    matches = list(
        NUMBER_PATTERN.finditer(
            output_lines[1].split("/", 1)[0]
        )
    )

    if len(matches) < 2:
        raise ValueError(
            "PWRTAB.PWR 두 번째 줄에서 "
            "두 번째 숫자 항목을 찾을 수 없습니다."
        )

    second = matches[1]

    output_lines[1] = (
        output_lines[1][:second.start()]
        + str(len(power_rows))
        + output_lines[1][second.end():]
    )

    power_header = ", ".join(
        f"'{name}'" for name in POWER_HEADERS
    ) + "/"

    output_lines.extend(["", power_header])
    output_lines.extend(
        f"{row}/" for row in power_rows
    )

    output_file.write_text(
        "\n".join(output_lines) + "\n",
        encoding="utf-8",
    )

    return len(power_rows)


# ============================================================
# bound.inp
# ============================================================

def update_bound_file(
    file_path,
    new_value,
    cp,
    source_label,
    line_number=2,
):
    if not file_path.is_file():
        raise FileNotFoundError(
            f"필수 파일이 없습니다: {file_path.name}"
        )

    lines = read_text(file_path).splitlines(keepends=True)

    if len(lines) < line_number:
        raise ValueError(
            f"{file_path.name}: {line_number}번째 줄이 없습니다."
        )

    line = lines[line_number - 1]

    code_part, slash, comment_part = line.partition("/")

    matches = list(
        NUMBER_PATTERN.finditer(code_part)
    )

    if len(matches) < 2:
        raise ValueError(
            f"{file_path.name} {line_number}번째 줄에서 "
            "두 번째 숫자 항목을 찾을 수 없습니다."
        )

    second = matches[1]

    code_part = (
        code_part[:second.start()]
        + str(new_value)
        + code_part[second.end():]
    )

    if slash:
        comment_part = re.sub(
            r"(?:hd\d+bc\.out|hc\d+bc\.out|"
            r"out\d+\.dat(?:\+out\d+\.dat)?)",
            source_label,
            comment_part,
            flags=re.IGNORECASE,
        )

        comment_part = re.sub(
            r"\(CP[1-4]\)",
            f"(CP{cp})",
            comment_part,
            flags=re.IGNORECASE,
        )

        if not re.search(
            r"\(CP[1-4]\)",
            comment_part,
            flags=re.IGNORECASE,
        ):
            comment_part = (
                comment_part.rstrip()
                + f" (CP{cp})"
            )

        line = code_part + slash + comment_part

    else:
        line = code_part

    lines[line_number - 1] = line

    file_path.write_text(
        "".join(lines),
        encoding="utf-8",
    )


# ============================================================
# CP selection and folder handling
# ============================================================

def select_cp():
    while True:
        answer = input(
            "실행할 CP 번호를 지정하세요 "
            "(예: 1 / 1,3 / 2,3,4 / ALL): "
        ).strip().upper()

        if answer in {"ALL", "A"}:
            return ["1", "2", "3", "4"]

        normalized = re.sub(
            r"CP\s*([1-4])",
            r"\1",
            answer,
        )

        parts = [
            part
            for part in re.split(r"[,;+;\s]+", normalized)
            if part
        ]

        if parts and all(part in CP_FILES for part in parts):
            return list(dict.fromkeys(parts))

        print(
            "잘못된 입력입니다. CP1~CP4 중 선택하세요."
        )


def remove_existing_cp_folders(working_dir):
    existing = [
        working_dir / f"CP{i}"
        for i in range(1, 5)
        if (working_dir / f"CP{i}").is_dir()
    ]

    if not existing:
        return True

    print("\n기존 CP 폴더가 발견되었습니다.")

    for folder in existing:
        print(f"  {folder.name}")

    answer = input(
        "기존 CP 폴더를 모두 삭제할까요? (Y/N): "
    ).strip().upper()

    if answer not in {"Y", "YES"}:
        print("작업을 중단합니다.")
        return False

    for folder in existing:
        shutil.rmtree(folder)

    return True


def create_cp_folders(working_dir, cp_numbers):
    """Create CP folders and copy only approved support files."""
    targets = {}

    for cp in cp_numbers:
        target = working_dir / f"CP{cp}"

        target.mkdir()

        copy_support_files(
            working_dir,
            target,
        )

        # bound.inp is required and is modified separately.
        bound_source = find_file_case_insensitive(
            working_dir,
            "bound.inp",
        )

        if bound_source:
            copy_file_if_different(
                bound_source,
                target / "bound.inp",
            )

        targets[cp] = target

    return targets


# ============================================================
# Channel input resolution
# ============================================================

def ask_channel_set_name():
    while True:
        name = input(
            "Channel 입력명(fn)을 입력하세요: "
        ).strip()

        if not name:
            print("입력명이 비어 있습니다.")
            continue

        if Path(name).name != name:
            print("파일명 또는 폴더명만 입력하세요.")
            continue

        if name.lower().endswith(".inp"):
            name = name[:-4]

        return name


def find_channel_input_files(channel_dir):
    """
    If fn.inp exists, use it.
    Otherwise use all top-level INP files in the folder.
    """
    if not channel_dir.is_dir():
        return []

    preferred = find_file_case_insensitive(
        channel_dir,
        f"{channel_dir.name}.inp",
    )

    if preferred:
        return [preferred]

    return sorted(
        [
            p for p in channel_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() == ".inp"
        ],
        key=lambda p: p.name.lower(),
    )


def resolve_channel_sources(working_dir, fn):
    """
    Supported locations:
      1. cwd/fn.inp
      2. cwd/fn/fn.inp
      3. cwd/fn/subfolder/*.inp
    """

    direct_input = find_file_case_insensitive(
        working_dir,
        f"{fn}.inp",
    )

    if direct_input:
        return [(direct_input.parent, [direct_input])]

    channel_root = working_dir / fn

    if not channel_root.is_dir():
        raise FileNotFoundError(
            f"{fn}.inp 또는 {fn} 폴더를 찾을 수 없습니다."
        )

    # First check for cwd/fn/fn.inp
    root_inputs = find_channel_input_files(channel_root)

    if root_inputs:
        return [(channel_root, root_inputs)]

    # Otherwise check its immediate subfolders.
    sources = []

    for folder in sorted(
        [p for p in channel_root.iterdir() if p.is_dir()],
        key=lambda p: p.name.lower(),
    ):
        inputs = find_channel_input_files(folder)

        if inputs:
            sources.append((folder, inputs))

    if not sources:
        raise FileNotFoundError(
            f"{channel_root}에서 .inp 파일을 찾을 수 없습니다."
        )

    return sources


# ============================================================
# Copy channel inputs and matching AUX
# ============================================================

def copy_channel_inputs(
    source_dir,
    input_files,
    target_dir,
    working_dir,
):
    """
    Copy channel INP files directly into target_dir.

    AUX rule:
      1. Same-name AUX in the INP source folder
      2. Otherwise cwd/base.aux
      3. If neither exists, continue without AUX
    """

    for inp_file in input_files:
        if not inp_file.is_file():
            continue

        if inp_file.suffix.lower() != ".inp":
            continue

        copy_file_if_different(
            inp_file,
            target_dir / inp_file.name,
        )

        aux_file = find_file_case_insensitive(
            source_dir,
            inp_file.stem + ".aux",
        )

        if aux_file is None:
            aux_file = find_file_case_insensitive(
                working_dir,
                "base.aux",
            )

        if aux_file is not None:
            aux_target = target_dir / (
                inp_file.stem + ".aux"
            )

            copy_file_if_different(
                aux_file,
                aux_target,
            )

    # Copy DAT files located alongside the channel inputs.
    copy_support_files(source_dir, target_dir)


# ============================================================
# Prepare one CP
# ============================================================

def prepare_one_cp(
    cp,
    target_dir,
    source_dir,
    power_source,
):
    bc_count, bc_source_label = create_cp_bc_table(
        cp,
        source_dir,
        target_dir / "rhdbcs.inp",
    )

    power_count = None

    if power_source is not None and power_source.is_file():
        power_count = create_pwrtab_file(
            target_dir / "PWRTAB.PWR",
            power_source,
            target_dir / "PWRTAB.PWR",
        )

    elif (target_dir / "PWRTAB.PWR").is_file():
        print(
            f"  CP{cp}: power.dat가 없어 "
            "기존 PWRTAB.PWR를 유지합니다."
        )

    else:
        raise FileNotFoundError(
            "power.dat와 기존 PWRTAB.PWR가 모두 없습니다."
        )

    update_bound_file(
        target_dir / "bound.inp",
        bc_count,
        cp,
        bc_source_label,
    )

    return bc_count, power_count, bc_source_label


# ============================================================
# Main
# ============================================================

def main():
    working_dir = Path.cwd()
    source_dir = working_dir.parent

    print("=" * 64)
    print(" Slave Channel Manager")
    print("=" * 64)
    print(f"Current folder : {working_dir}")
    print(f"BC source      : {source_dir}")
    print()

    if not remove_existing_cp_folders(working_dir):
        return

    selected_cps = select_cp()

    fn = ask_channel_set_name()

    channel_sources = resolve_channel_sources(
        working_dir,
        fn,
    )

    power_source = source_dir / "power.dat"

    if power_source.is_file():
        read_numeric_rows(power_source, 2)

    elif not (working_dir / "PWRTAB.PWR").is_file():
        raise FileNotFoundError(
            "상위 폴더의 power.dat와 현재 폴더의 "
            "PWRTAB.PWR가 모두 없습니다."
        )

    # --------------------------------------------------------
    # Validate each CP independently
    # --------------------------------------------------------

    print("\nCP 입력 데이터 확인...")

    valid_cps = []

    for cp in selected_cps:
        try:
            if validate_cp_source(cp, source_dir):
                valid_cps.append(cp)
                print(f"  CP{cp}: OK")

        except (ValueError, FileNotFoundError, OSError) as error:
            print(
                f"  WARNING CP{cp}: {error} "
                "-- 이 CP는 skip합니다."
            )

    if not valid_cps:
        print("\n유효한 CP가 없습니다. 작업을 종료합니다.")
        return

    # One valid CP: use current folder.
    # Two or more valid CPs: create separate CP folders.
    if len(valid_cps) == 1:
        target_dirs = {
            valid_cps[0]: working_dir
        }

    else:
        target_dirs = create_cp_folders(
            working_dir,
            valid_cps,
        )

    # --------------------------------------------------------
    # Prepare boundary and power inputs
    # --------------------------------------------------------

    print("\nCP별 입력 파일 준비...")

    prepared_cps = []

    for cp in valid_cps:
        target_dir = target_dirs[cp]

        try:
            bc_count, power_count, bc_source = prepare_one_cp(
                cp,
                target_dir,
                source_dir,
                power_source if power_source.is_file() else None,
            )

            prepared_cps.append(cp)

            power_status = (
                f"power rows={power_count}"
                if power_count is not None
                else "PWRTAB.PWR 유지"
            )

            print(
                f"  CP{cp}: RHDBCS={bc_count} rows, "
                f"source={bc_source}, {power_status}"
            )

        except (ValueError, FileNotFoundError, OSError) as error:
            print(
                f"  ERROR CP{cp}: {error} "
                "-- 이 CP는 skip합니다."
            )

            # Delete only a CP folder created by this run.
            if len(valid_cps) > 1 and target_dir.exists():
                shutil.rmtree(target_dir)

    if not prepared_cps:
        print("\n입력 준비에 성공한 CP가 없습니다.")
        return

    # --------------------------------------------------------
    # Copy channel inputs into each CP root
    # --------------------------------------------------------

    print("\nChannel 입력 파일 복사...")

    for cp in prepared_cps:
        target_dir = target_dirs[cp]

        for source_dir_for_channel, input_files in channel_sources:
            copy_channel_inputs(
                source_dir_for_channel,
                input_files,
                target_dir,
                working_dir,
            )

            for inp_file in input_files:
                print(
                    f"  CP{cp}: {inp_file.name} 복사 완료"
                )

    print("\n입력 파일 준비가 완료되었습니다.")
    print("runcat 실행 및 완료 모니터링은 아직 포함되어 있지 않습니다.")


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print("\n사용자에 의해 작업이 중단되었습니다.")
        sys.exit(1)

    except Exception as error:
        print(f"\nERROR: {error}")
        sys.exit(1)
