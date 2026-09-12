//! Scripted network for the fetch tests.
//!
//! Every test in this crate runs against these, never a socket, so a case like
//! "a redirect to the metadata endpoint is refused" is checked by asserting
//! that nothing ever tried to connect to it.

use std::collections::HashMap;
use std::io::{Cursor, Read};
use std::net::IpAddr;
use std::sync::Mutex;
use std::time::{Duration, SystemTime};

use hearthai_web_fetch_worker::FetchError;
use hearthai_web_fetch_worker::clock::Clock;
use hearthai_web_fetch_worker::net::{Connector, HopResponse, Resolver};
use url::Url;

#[derive(Default)]
pub struct ScriptedResolver {
    answers: HashMap<String, Vec<IpAddr>>,
    pub looked_up: Mutex<Vec<String>>,
}

impl ScriptedResolver {
    pub fn with(host: &str, addresses: &[&str]) -> Self {
        let mut resolver = Self::default();
        resolver.add(host, addresses);
        resolver
    }

    pub fn add(&mut self, host: &str, addresses: &[&str]) -> &mut Self {
        self.answers.insert(host.to_owned(), addresses.iter().map(|entry| entry.parse().unwrap()).collect());
        self
    }
}

impl Resolver for ScriptedResolver {
    fn resolve(&self, host: &str, _budget: Duration) -> Result<Vec<IpAddr>, FetchError> {
        self.looked_up.lock().unwrap().push(host.to_owned());
        self.answers.get(host).cloned().ok_or(FetchError::Network)
    }
}

#[derive(Clone)]
pub struct Reply {
    pub status: u16,
    pub header_bytes: usize,
    pub content_type: Option<String>,
    pub content_encoding: Option<String>,
    pub location: Option<String>,
    pub body: Vec<u8>,
}

impl Reply {
    pub fn html(body: &str) -> Self {
        Self {
            status: 200,
            header_bytes: 256,
            content_type: Some("text/html; charset=utf-8".into()),
            content_encoding: None,
            location: None,
            body: body.as_bytes().to_vec(),
        }
    }

    pub fn redirect(status: u16, location: &str) -> Self {
        Self {
            status,
            header_bytes: 256,
            content_type: None,
            content_encoding: None,
            location: Some(location.into()),
            body: Vec::new(),
        }
    }

    pub fn typed(content_type: &str, body: &[u8]) -> Self {
        Self {
            status: 200,
            header_bytes: 256,
            content_type: Some(content_type.into()),
            content_encoding: None,
            location: None,
            body: body.to_vec(),
        }
    }

    pub fn with_header_bytes(mut self, bytes: usize) -> Self {
        self.header_bytes = bytes;
        self
    }
}

#[derive(Default)]
pub struct ScriptedConnector {
    replies: Mutex<HashMap<String, Vec<Reply>>>,
    pub contacted: Mutex<Vec<(String, IpAddr)>>,
}

impl ScriptedConnector {
    pub fn new(replies: Vec<(&str, Reply)>) -> Self {
        let connector = Self::default();
        for (url, reply) in replies {
            connector.replies.lock().unwrap().entry(url.to_owned()).or_default().push(reply);
        }
        connector
    }

    pub fn addresses_contacted(&self) -> Vec<IpAddr> {
        self.contacted.lock().unwrap().iter().map(|(_, address)| *address).collect()
    }
}

impl Connector for ScriptedConnector {
    fn get(&self, url: &Url, address: IpAddr, _budget: Duration) -> Result<HopResponse, FetchError> {
        self.contacted.lock().unwrap().push((url.as_str().to_owned(), address));
        let mut replies = self.replies.lock().unwrap();
        let queue = replies.get_mut(url.as_str()).ok_or(FetchError::Network)?;
        // A queue of more than one lets a test script a second, different
        // answer for the same URL; a single entry is served repeatedly.
        let reply = if queue.len() > 1 {
            queue.remove(0)
        } else {
            queue.first().cloned().ok_or(FetchError::Network)?
        };
        let body: Box<dyn Read> = Box::new(Cursor::new(reply.body));
        Ok(HopResponse {
            status: reply.status,
            header_bytes: reply.header_bytes,
            content_type: reply.content_type,
            content_encoding: reply.content_encoding,
            location: reply.location,
            body,
        })
    }
}

/// A clock that only moves when a test moves it.
pub struct StepClock {
    start: SystemTime,
    step: Duration,
    calls: Mutex<u32>,
}

impl StepClock {
    pub fn new(step: Duration) -> Self {
        Self {
            start: SystemTime::UNIX_EPOCH + Duration::from_secs(1_788_000_000),
            step,
            calls: Mutex::new(0),
        }
    }
}

impl Clock for StepClock {
    fn now(&self) -> SystemTime {
        let mut calls = self.calls.lock().unwrap();
        let moment = self.start + self.step * *calls;
        *calls += 1;
        moment
    }
}
