CREATE VECTOR INDEX IF NOT EXISTS beliefs_embedding_idx ON beliefs (embedding vector_cosine_ops);
