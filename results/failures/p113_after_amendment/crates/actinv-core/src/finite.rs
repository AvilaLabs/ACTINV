//! Refuse non-finite numbers in a result before anything writes it. serde_json serialises NaN and
//! ±inf as `null`, which a reader cannot tell from an absent value, so a numerical failure would
//! otherwise leave a run looking as if it succeeded.
use serde::ser::{self, Serialize};
use std::fmt;

/// `Err` names the first non-finite number as a path, such as `steps[3].heat_W_per_g.total is NaN`.
pub fn check<T: Serialize + ?Sized>(value: &T) -> Result<(), String> {
    value
        .serialize(&mut Walker { path: Vec::new() })
        .map_err(|found| found.0)
}

enum Segment {
    Field(&'static str),
    Index(usize),
    Key(String),
}

struct Walker {
    path: Vec<Segment>,
}

#[derive(Debug)]
struct Found(String);

impl fmt::Display for Found {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Found {}

impl ser::Error for Found {
    fn custom<T: fmt::Display>(message: T) -> Self {
        Found(message.to_string())
    }
}

impl Walker {
    fn number(&self, value: f64) -> Result<(), Found> {
        if value.is_finite() {
            return Ok(());
        }
        let mut path = String::new();
        for segment in &self.path {
            match segment {
                Segment::Field(name) => {
                    if !path.is_empty() {
                        path.push('.');
                    }
                    path.push_str(name);
                }
                Segment::Index(index) => path.push_str(&format!("[{index}]")),
                Segment::Key(key) => path.push_str(&format!("[{key:?}]")),
            }
        }
        if path.is_empty() {
            path.push_str("the value");
        }
        Err(Found(format!("{path} is {value}")))
    }

    fn nested<T: Serialize + ?Sized>(&mut self, segment: Segment, value: &T) -> Result<(), Found> {
        self.path.push(segment);
        let result = value.serialize(&mut *self);
        self.path.pop();
        result
    }
}

struct Compound<'a> {
    walker: &'a mut Walker,
    index: usize,
    key: Option<String>,
    variant: bool,
}

impl<'a> Compound<'a> {
    fn new(walker: &'a mut Walker) -> Self {
        Compound {
            walker,
            index: 0,
            key: None,
            variant: false,
        }
    }

    fn variant(walker: &'a mut Walker, name: &'static str) -> Self {
        walker.path.push(Segment::Field(name));
        Compound {
            variant: true,
            ..Compound::new(walker)
        }
    }

    fn element<T: Serialize + ?Sized>(&mut self, value: &T) -> Result<(), Found> {
        let index = self.index;
        self.index += 1;
        self.walker.nested(Segment::Index(index), value)
    }

    fn finish(self) -> Result<(), Found> {
        if self.variant {
            self.walker.path.pop();
        }
        Ok(())
    }
}

impl<'a> ser::Serializer for &'a mut Walker {
    type Ok = ();
    type Error = Found;
    type SerializeSeq = Compound<'a>;
    type SerializeTuple = Compound<'a>;
    type SerializeTupleStruct = Compound<'a>;
    type SerializeTupleVariant = Compound<'a>;
    type SerializeMap = Compound<'a>;
    type SerializeStruct = Compound<'a>;
    type SerializeStructVariant = Compound<'a>;

    fn serialize_bool(self, _: bool) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_i8(self, _: i8) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_i16(self, _: i16) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_i32(self, _: i32) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_i64(self, _: i64) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_i128(self, _: i128) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_u8(self, _: u8) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_u16(self, _: u16) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_u32(self, _: u32) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_u64(self, _: u64) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_u128(self, _: u128) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_f32(self, value: f32) -> Result<(), Found> {
        self.number(f64::from(value))
    }
    fn serialize_f64(self, value: f64) -> Result<(), Found> {
        self.number(value)
    }
    fn serialize_char(self, _: char) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_str(self, _: &str) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_bytes(self, _: &[u8]) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_none(self) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_some<T: Serialize + ?Sized>(self, value: &T) -> Result<(), Found> {
        value.serialize(self)
    }
    fn serialize_unit(self) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_unit_struct(self, _: &'static str) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_unit_variant(self, _: &'static str, _: u32, _: &'static str) -> Result<(), Found> {
        Ok(())
    }
    fn serialize_newtype_struct<T: Serialize + ?Sized>(
        self,
        _: &'static str,
        value: &T,
    ) -> Result<(), Found> {
        value.serialize(self)
    }
    fn serialize_newtype_variant<T: Serialize + ?Sized>(
        self,
        _: &'static str,
        _: u32,
        variant: &'static str,
        value: &T,
    ) -> Result<(), Found> {
        self.nested(Segment::Field(variant), value)
    }
    fn serialize_seq(self, _: Option<usize>) -> Result<Compound<'a>, Found> {
        Ok(Compound::new(self))
    }
    fn serialize_tuple(self, _: usize) -> Result<Compound<'a>, Found> {
        Ok(Compound::new(self))
    }
    fn serialize_tuple_struct(self, _: &'static str, _: usize) -> Result<Compound<'a>, Found> {
        Ok(Compound::new(self))
    }
    fn serialize_tuple_variant(
        self,
        _: &'static str,
        _: u32,
        variant: &'static str,
        _: usize,
    ) -> Result<Compound<'a>, Found> {
        Ok(Compound::variant(self, variant))
    }
    fn serialize_map(self, _: Option<usize>) -> Result<Compound<'a>, Found> {
        Ok(Compound::new(self))
    }
    fn serialize_struct(self, _: &'static str, _: usize) -> Result<Compound<'a>, Found> {
        Ok(Compound::new(self))
    }
    fn serialize_struct_variant(
        self,
        _: &'static str,
        _: u32,
        variant: &'static str,
        _: usize,
    ) -> Result<Compound<'a>, Found> {
        Ok(Compound::variant(self, variant))
    }
}

impl ser::SerializeSeq for Compound<'_> {
    type Ok = ();
    type Error = Found;
    fn serialize_element<T: Serialize + ?Sized>(&mut self, value: &T) -> Result<(), Found> {
        self.element(value)
    }
    fn end(self) -> Result<(), Found> {
        self.finish()
    }
}

impl ser::SerializeTuple for Compound<'_> {
    type Ok = ();
    type Error = Found;
    fn serialize_element<T: Serialize + ?Sized>(&mut self, value: &T) -> Result<(), Found> {
        self.element(value)
    }
    fn end(self) -> Result<(), Found> {
        self.finish()
    }
}

impl ser::SerializeTupleStruct for Compound<'_> {
    type Ok = ();
    type Error = Found;
    fn serialize_field<T: Serialize + ?Sized>(&mut self, value: &T) -> Result<(), Found> {
        self.element(value)
    }
    fn end(self) -> Result<(), Found> {
        self.finish()
    }
}

impl ser::SerializeTupleVariant for Compound<'_> {
    type Ok = ();
    type Error = Found;
    fn serialize_field<T: Serialize + ?Sized>(&mut self, value: &T) -> Result<(), Found> {
        self.element(value)
    }
    fn end(self) -> Result<(), Found> {
        self.finish()
    }
}

impl ser::SerializeMap for Compound<'_> {
    type Ok = ();
    type Error = Found;
    fn serialize_key<T: Serialize + ?Sized>(&mut self, key: &T) -> Result<(), Found> {
        self.key = Some(match serde_json::to_value(key) {
            Ok(serde_json::Value::String(text)) => text,
            Ok(other) => other.to_string(),
            Err(_) => "?".into(),
        });
        Ok(())
    }
    fn serialize_value<T: Serialize + ?Sized>(&mut self, value: &T) -> Result<(), Found> {
        let key = self.key.take().unwrap_or_default();
        self.walker.nested(Segment::Key(key), value)
    }
    fn end(self) -> Result<(), Found> {
        self.finish()
    }
}

impl ser::SerializeStruct for Compound<'_> {
    type Ok = ();
    type Error = Found;
    fn serialize_field<T: Serialize + ?Sized>(
        &mut self,
        name: &'static str,
        value: &T,
    ) -> Result<(), Found> {
        self.walker.nested(Segment::Field(name), value)
    }
    fn end(self) -> Result<(), Found> {
        self.finish()
    }
}

impl ser::SerializeStructVariant for Compound<'_> {
    type Ok = ();
    type Error = Found;
    fn serialize_field<T: Serialize + ?Sized>(
        &mut self,
        name: &'static str,
        value: &T,
    ) -> Result<(), Found> {
        self.walker.nested(Segment::Field(name), value)
    }
    fn end(self) -> Result<(), Found> {
        self.finish()
    }
}

#[cfg(test)]
mod tests {
    use super::check;
    use std::collections::BTreeMap;

    #[derive(serde::Serialize)]
    struct Step {
        t_s: f64,
        activity: BTreeMap<String, f64>,
        uncertainty: Option<Vec<(f64, f64)>>,
    }

    #[derive(serde::Serialize)]
    enum Shape {
        Pair(f64, f64),
        Named { weight: f64 },
    }

    fn step(activity: f64, sigma: f64) -> Step {
        Step {
            t_s: 1.0,
            activity: BTreeMap::from([("Fe55".to_string(), activity)]),
            uncertainty: Some(vec![(1.0, 2.0), (3.0, sigma)]),
        }
    }

    #[test]
    fn non_finite_numbers_are_named_by_path_and_finite_results_pass() {
        let finite = vec![step(1.0, 0.5), step(-0.0, f64::MIN_POSITIVE / 4.0)];
        assert_eq!(check(&finite), Ok(()));
        assert_eq!(
            check(&serde_json::json!({"a": [1.0, {"b": 2}], "c": null})),
            Ok(())
        );

        let nan_key = vec![step(1.0, 0.5), step(f64::NAN, 0.5)];
        assert_eq!(check(&nan_key), Err("[1].activity[\"Fe55\"] is NaN".into()));
        let inf_tuple = vec![step(1.0, f64::INFINITY)];
        assert_eq!(
            check(&inf_tuple),
            Err("[0].uncertainty[1][1] is inf".into())
        );
        assert_eq!(check(&f64::NEG_INFINITY), Err("the value is -inf".into()));
        assert_eq!(
            check(&vec![
                Shape::Pair(0.0, 1.0),
                Shape::Named { weight: f64::NAN }
            ]),
            Err("[1].Named.weight is NaN".into())
        );
        assert_eq!(
            check(&[Shape::Pair(0.0, f64::NAN)]),
            Err("[0].Pair[1] is NaN".into())
        );
    }
}
