# Frontend Deployment Guide

## Quick Start

### Local Development

```bash
cd frontend
npm install
npm run dev
```

Dashboard will be available at `http://localhost:3000`

### Connect to Backend

The frontend requires a backend API running on `http://localhost:8000`. To connect to a different backend:

```bash
# Set environment variable before running
export NEXT_PUBLIC_API_URL=http://backend-url:8000
npm run dev
```

Or create `.env.local`:

```env
NEXT_PUBLIC_API_URL=http://your-backend:8000
```

## Production Deployment

### Build

```bash
npm run build
npm start
```

The production server will start on port 3000 (or `$PORT` env var if set).

### Docker

#### Build Image

```bash
docker build -t botcrypto4-frontend .
```

#### Run Container

```bash
docker run -p 3000:3000 \
  -e NEXT_PUBLIC_API_URL=http://backend:8000 \
  botcrypto4-frontend
```

#### Docker Compose

Add to your `docker-compose.yml`:

```yaml
services:
  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    environment:
      NEXT_PUBLIC_API_URL: http://backend:8000
    depends_on:
      - backend
```

Then run:

```bash
docker-compose up frontend
```

## Environment Variables

### Required

**None** - Backend defaults to `http://localhost:8000`

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend API URL |
| `NODE_ENV` | `production` | Node environment (development/production) |

## Configuration

### Backend URL Resolution

The frontend uses the `NEXT_PUBLIC_API_URL` environment variable to determine the backend URL:

1. If set, uses the provided URL
2. Automatically removes trailing slashes
3. Uses `http://localhost:8000` as fallback

Example in code:

```typescript
const baseUrl = sanitizeBaseUrl(
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
);
```

### API Polling Intervals

To customize polling rates, edit `dashboard-client.tsx`:

```typescript
const CONTEXT_POLL_INTERVAL = 7000;    // 7 seconds
const HEALTH_POLL_INTERVAL = 5000;     // 5 seconds
const METRICS_POLL_INTERVAL = 2000;    // 2 seconds
const PRICE_POLL_INTERVAL = 1000;      // 1 second
```

## Deployment Platforms

### Vercel (Recommended for Next.js)

1. Push code to GitHub
2. Connect Vercel to GitHub repository
3. Add environment variables in Vercel dashboard:
   ```
   NEXT_PUBLIC_API_URL=https://api.your-domain.com
   ```
4. Deploy automatically on push

[Vercel Next.js Guide](https://vercel.com/docs/frameworks/nextjs)

### Netlify

```bash
# Build locally
npm run build

# Or use Netlify CLI
netlify deploy --prod --dir=.next
```

Create `netlify.toml`:

```toml
[build]
  command = "npm run build"
  publish = ".next"

[env.production]
  NEXT_PUBLIC_API_URL = "https://api.your-domain.com"
```

### AWS (EC2/ECS)

#### EC2 with PM2

```bash
# Install PM2
npm install -g pm2

# Build
npm run build

# Start
pm2 start npm --name "frontend" -- start

# Save PM2 config
pm2 save
```

#### ECS/Fargate

Use the provided Dockerfile:

```bash
docker build -t botcrypto4-frontend:latest .
docker tag botcrypto4-frontend:latest <account>.dkr.ecr.<region>.amazonaws.com/botcrypto4-frontend:latest
docker push <account>.dkr.ecr.<region>.amazonaws.com/botcrypto4-frontend:latest
```

### Kubernetes

Create `k8s-deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: botcrypto4-frontend
spec:
  replicas: 2
  selector:
    matchLabels:
      app: botcrypto4-frontend
  template:
    metadata:
      labels:
        app: botcrypto4-frontend
    spec:
      containers:
      - name: frontend
        image: botcrypto4-frontend:latest
        ports:
        - containerPort: 3000
        env:
        - name: NEXT_PUBLIC_API_URL
          value: "http://backend-service:8000"
        livenessProbe:
          httpGet:
            path: /
            port: 3000
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /
            port: 3000
          initialDelaySeconds: 5
          periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: frontend-service
spec:
  selector:
    app: botcrypto4-frontend
  ports:
  - port: 80
    targetPort: 3000
  type: LoadBalancer
```

Deploy:

```bash
kubectl apply -f k8s-deployment.yaml
```

## CORS Configuration

### Frontend CORS

Next.js doesn't require CORS configuration - it's a server-side platform.

### Backend CORS

Ensure your backend allows requests from the frontend domain. In FastAPI:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",      # Development
        "https://dashboard.your-domain.com",  # Production
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## SSL/TLS

### Using Nginx as Reverse Proxy

```nginx
server {
    listen 443 ssl http2;
    server_name dashboard.your-domain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Monitoring & Logs

### Application Logs

The frontend logs to stdout/stderr. Capture with:

```bash
npm start 2>&1 | tee app.log
```

Or with PM2:

```bash
pm2 logs frontend
```

### Health Check

Verify the application is running:

```bash
curl http://localhost:3000
```

Should return HTML response (not error).

### Metrics

Monitor these metrics:

- **Response Time**: Dashboard page load time
- **API Success Rate**: Percentage of successful API calls
- **Error Rate**: Frontend errors (check browser console)
- **Memory Usage**: Process memory consumption
- **CPU Usage**: Process CPU load

### Example Nginx Monitoring

```nginx
location /health {
    access_log off;
    return 200 "ok";
    add_header Content-Type text/plain;
}
```

## Performance Tuning

### 1. Enable Gzip Compression

In Next.js, gzip is enabled by default. Verify with:

```bash
curl -I -H "Accept-Encoding: gzip" http://localhost:3000
# Should see: Content-Encoding: gzip
```

### 2. Browser Caching

Add to Nginx:

```nginx
location ~* \.(js|css|png|jpg|jpeg|gif|svg|woff|woff2|ttf|eot)$ {
    expires 1y;
    add_header Cache-Control "public, immutable";
}
```

### 3. Reduce Polling Intervals

Edit `dashboard-client.tsx` if network bandwidth is limited:

```typescript
const METRICS_POLL_INTERVAL = 5000;  // Increase from 2000ms
```

### 4. Lazy Load Components

For future enhancement with heavy charting libraries:

```typescript
const Chart = dynamic(() => import('@/components/Chart'), {
  loading: () => <p>Loading chart...</p>,
  ssr: false,
});
```

## Troubleshooting

### Port 3000 Already in Use

```bash
# Find process using port 3000
lsof -i :3000

# Kill process
kill -9 <PID>

# Or use different port
PORT=3001 npm start
```

### Backend Connection Errors

1. Verify backend is running:
   ```bash
   curl http://localhost:8000/health
   ```

2. Check `NEXT_PUBLIC_API_URL` is correct

3. Verify CORS is enabled on backend

4. Check firewall rules

### Build Failures

1. Clear Next.js cache:
   ```bash
   rm -rf .next
   ```

2. Reinstall dependencies:
   ```bash
   rm -rf node_modules
   npm install
   ```

3. Check Node.js version (18+):
   ```bash
   node --version
   ```

### High Memory Usage

1. Reduce polling frequency
2. Limit price chart history (`MAX_PRICE_POINTS`)
3. Check for memory leaks in browser DevTools
4. Restart application

## Security Considerations

1. **Environment Variables**: Never commit `.env.local` - use `.env.example`
2. **API Keys**: If backend requires auth, configure in env vars
3. **HTTPS**: Always use HTTPS in production
4. **CSP Headers**: Configure Content Security Policy headers
5. **Dependency Updates**: Run `npm audit fix` regularly

### Example .env.example

```env
# Backend API URL
NEXT_PUBLIC_API_URL=http://your-backend:8000

# Optional authentication
# BACKEND_API_KEY=your-api-key-here
```

## Rollback

### Docker

```bash
# Run previous version
docker run -p 3000:3000 botcrypto4-frontend:v1.0.0
```

### Kubernetes

```bash
kubectl set image deployment/botcrypto4-frontend \
  frontend=botcrypto4-frontend:v1.0.0 --record
```

### Direct

```bash
# Checkout previous build
git checkout <previous-hash>
npm run build
npm start
```

## CI/CD Integration

### GitHub Actions

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy Frontend

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-node@v2
        with:
          node-version: '20'
      - run: cd frontend && npm install
      - run: cd frontend && npm run lint
      - run: cd frontend && npm run build
      - name: Deploy to Vercel
        uses: vercel/action@master
        with:
          vercel-token: ${{ secrets.VERCEL_TOKEN }}
          vercel-org-id: ${{ secrets.VERCEL_ORG_ID }}
          vercel-project-id: ${{ secrets.VERCEL_PROJECT_ID }}
```

## Support

For issues or questions, refer to:

1. [Next.js Documentation](https://nextjs.org/docs)
2. [Frontend README](./README.md)
3. [Frontend Audit](./AUDIT.md)
4. Project issue tracker
