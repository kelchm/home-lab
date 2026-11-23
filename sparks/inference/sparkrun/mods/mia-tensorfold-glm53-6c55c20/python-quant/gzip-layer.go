package main

import (
	"compress/gzip"
	"io"
	"log"
	"os"
)

func main() {
	output := gzip.NewWriter(os.Stdout)
	if _, err := io.Copy(output, os.Stdin); err != nil {
		log.Fatal(err)
	}
	if err := output.Close(); err != nil {
		log.Fatal(err)
	}
}
