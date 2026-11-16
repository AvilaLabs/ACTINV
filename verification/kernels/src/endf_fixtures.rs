//! Small synthetic records shared by executable regressions and proof harnesses.

pub const VALID_RECORD: &str = concat!(
    "                                                                  ",
    "   1 1  1",
);

const fn non_ascii_bytes<const N: usize>() -> [[u8; N]; 65] {
    assert!(N == 66 || N == 75);
    let mut records = [[b' '; N]; 65];
    let mut position = 0;
    while position < records.len() {
        records[position][position] = 0xc2;
        records[position][position + 1] = 0xa0;
        if N == 75 {
            let tail = b"   1 1  1";
            let mut index = 0;
            while index < tail.len() {
                records[position][66 + index] = tail[index];
                index += 1;
            }
        }
        position += 1;
    }
    records
}

const fn record_views<const N: usize>(records: &[[u8; N]; 65]) -> [&str; 65] {
    let mut result = [""; 65];
    let mut index = 0;
    while index < records.len() {
        result[index] = match std::str::from_utf8(&records[index]) {
            Ok(text) => text,
            Err(_) => panic!("invalid synthetic UTF-8 record"),
        };
        index += 1;
    }
    result
}

const NON_ASCII_BYTES: [[u8; 66]; 65] = non_ascii_bytes();
pub const NON_ASCII_RECORDS: [&str; 65] = record_views(&NON_ASCII_BYTES);
const NON_ASCII_TAIL_BYTES: [[u8; 75]; 65] = non_ascii_bytes();
pub const NON_ASCII_TAIL_RECORDS: [&str; 65] = record_views(&NON_ASCII_TAIL_BYTES);
