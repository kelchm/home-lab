// Recovered from preserved reviewer stderr.log; original /tmp/modelkeep-probe source is gone.
// Base v0.4.12: 535dbbeab9acbfcecb93f5be4fea092c3b29d7db.
// In-module fixture fragment only; not a standalone test crate. No fresh test execution.
    struct CountingSlowFetcher { calls: Arc<AtomicUsize> }
    impl UpstreamFetcher for CountingSlowFetcher {
        fn fetch(&self, request: &FetchRequest) -> Result<FetchedRevision, UpstreamError> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            std::thread::sleep(Duration::from_millis(300));
            for f in &request.files {
                fs::write(request.staging.join(f), b"payload").unwrap();
            }
            Ok(FetchedRevision {
                commit: "cccccccccccccccccccccccccccccccccccccccc".into(),
                files: request.files.clone(),
                staging: request.staging.clone(),
            })
        }
    }

    #[test]
    fn audit_probe_same_file_by_ref_and_by_commit_transfers_twice() {
        let root = tempfile::tempdir().unwrap();
        let archive = Archive::new(root.path()).unwrap();
        let calls = Arc::new(AtomicUsize::new(0));
        let pull = Arc::new(PullThrough::new(archive.clone(), Arc::new(CountingSlowFetcher { calls: calls.clone() })));
        let a = { let p = pull.clone(); std::thread::spawn(move || p.ensure("org/model", "main", &["big.bin".into()]).unwrap()) };
        std::thread::sleep(Duration::from_millis(50));
        let b = { let p = pull.clone(); std::thread::spawn(move || p.ensure("org/model", "cccccccccccccccccccccccccccccccccccccccc", &["big.bin".into()]).unwrap()) };
        a.join().unwrap(); b.join().unwrap();
        eprintln!("upstream transfers for one file: {}", calls.load(Ordering::SeqCst));
    }
